from pritunl.constants import *
from pritunl.exceptions import *
from pritunl.helpers import *
from pritunl import utils
from pritunl import server
from pritunl import organization
from pritunl import app
from pritunl import auth

import os
import flask
import tarfile
import time

def tar_add(tar_file, path):
    if os.path.exists(path):
        tar_file.add(path, arcname=os.path.relpath(path, app_server.data_path))

@app.app.route('/export', methods=['GET'])
@app.app.route('/export/%s.tar' % APP_NAME, methods=['GET'])
@auth.session_auth
def export_get():
    data_path = app_server.data_path
    temp_path = os.path.join(data_path, TEMP_DIR)
    empty_temp_path = os.path.join(temp_path, EMPTY_TEMP_DIR)
    data_archive_name = '%s_%s.tar' % (APP_NAME,
        time.strftime('%Y_%m_%d_%H_%M_%S', time.localtime()))
    data_archive_path = os.path.join(temp_path, data_archive_name)

    # Create empty temp directory to recreate temp dirs in tarfile
    if not os.path.exists(empty_temp_path):
        os.makedirs(empty_temp_path)

    tar_file = tarfile.open(data_archive_path, 'w')
    try:
        tar_add(tar_file, os.path.join(data_path, AUTH_LOG_NAME))
        tar_add(tar_file, os.path.join(data_path, 'pritunl.db'))
        tar_add(tar_file, os.path.join(data_path, SERVER_CERT_NAME))
        tar_add(tar_file, os.path.join(data_path, SERVER_KEY_NAME))
        tar_add(tar_file, os.path.join(data_path, VERSION_NAME))

        for org in organization.iter_orgs():
            tar_add(tar_file, org.get_path())
            tar_file.add(empty_temp_path,
                arcname=os.path.relpath(os.path.join(org.path, TEMP_DIR),
                    data_path))

            for user in org.iter_users():
                tar_add(tar_file, user.reqs_path)
                tar_add(tar_file, user.key_path)
                tar_add(tar_file, user.cert_path)
                tar_add(tar_file, user.get_path())

            tar_add(tar_file, org.ca_cert.reqs_path)
            tar_add(tar_file, org.ca_cert.key_path)
            tar_add(tar_file, org.ca_cert.cert_path)
            tar_add(tar_file, org.ca_cert.get_path())

        for svr in server.iter_servers():
            tar_add(tar_file, svr.dh_param_path)
            tar_add(tar_file, svr.ip_pool_path)
            tar_add(tar_file, svr.get_path())
            tar_add(tar_file, os.path.join(svr.path, NODE_SERVER))
            tar_file.add(empty_temp_path,
                arcname=os.path.relpath(os.path.join(svr.path, TEMP_DIR),
                    data_path))

        tar_file.close()

        with open(data_archive_path, 'r') as archive_file:
            response = flask.Response(response=archive_file.read(),
                mimetype='application/octet-stream')
            response.headers.add('Content-Disposition',
                'attachment; filename="%s"' % data_archive_name)
        return response
    finally:
        try:
            tar_file.close()
        except OSError:
            pass
        try:
            os.remove(data_archive_path)
        except OSError:
            pass
