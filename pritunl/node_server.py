from constants import *
from exceptions import *
from pritunl import app_server
from server import Server
from organization import Organization
from event import Event
from log_entry import LogEntry
from cache import cache_db
import httplib
import logging
import os
import json
import utils
import threading
import time

logger = logging.getLogger(APP_NAME)

class NodeServer(Server):
    str_options = Server.str_options | {'node_ip', 'node_key'}
    int_options = Server.int_options | {'node_port'}
    default_options = dict(Server.default_options.items() + {
        'node_port': 9800,
    }.items())
    type = NODE_SERVER_NAME

    def dict(self):
        server_dict = Server.dict(self)
        server_dict['node_ip'] = self.node_ip
        server_dict['node_port'] = self.node_port
        server_dict['node_key'] = self.node_key
        return server_dict

    def _initialize(self):
        Server._initialize(self)
        with open(os.path.join(self.path, NODE_SERVER_NAME), 'w'):
            pass

    def _request(self, method, endpoint='', timeout=HTTP_REQUEST_TIMEOUT,
            json_data=None):
        return getattr(utils.request, method)(
            self._get_node_url() + endpoint,
            timeout=timeout,
            headers={
                'API-Key': self.node_key,
            },
            json_data=json_data,
        )

    def _get_node_url(self):
        return 'https://%s:%s/server/%s' % (
            self.node_ip, self.node_port, self.id)

    def _com_thread(self):
        responses = []
        trigger_event = False

        try:
            while not self._interrupt and not app_server.interrupt:
                try:
                    response = self._request('put', endpoint='/com',
                        timeout=HTTP_COM_REQUEST_TIMEOUT, json_data=responses)
                    if response.status_code == 200:
                        pass
                    elif response.status_code == 410:
                        break
                    else:
                        logger.exception('Error with node server ' + \
                            'connection occurred. %r' % {
                                'server_id': self.id,
                                'status_code': response.status_code,
                                'reason': response.reason,
                            })
                        LogEntry(message='Error with node server ' + \
                            'connection occurred "%s".' % self.name)
                        trigger_event = True
                        break
                    calls = response.json()
                    responses = []

                    for call in calls:
                        try:
                            responses.append({
                                'id': call['id'],
                                'response': getattr(self, call['command'])(
                                    *call['args']),
                            })
                        except:
                            logger.exception('Node server com thread call ' + \
                                'failed. %s' % {
                                    'server_id': self.id,
                                    'call_id': call['id'],
                                    'call_command': call['command'],
                                    'call_args': call['args'],
                                })
                except httplib.HTTPException:
                    logger.exception('Lost connection with node server. %r' % {
                        'server_id': self.id,
                    })
                    LogEntry(message='Lost connection with node ' + \
                        'server "%s".' % self.name)
                    trigger_event = True
        finally:
            self.status = False
            if trigger_event:
                Event(type=SERVERS_UPDATED)
                LogEntry(message='Stopped server "%s".' % self.name)

    def tls_verify(self, org_id, user_id):
        org = self.get_org(org_id)
        if not org:
            LogEntry(message='User failed authentication, ' +
                'invalid organization "%s".' % server.name)
            return False
        user = org.get_user(user_id)
        if not user:
            LogEntry(message='User failed authentication, ' +
                'invalid user "%s".' % server.name)
            return False
        return True

    def otp_verify(self, org_id, user_id, otp_code):
        org = self.get_org(org_id)
        if not org:
            LogEntry(message='User failed authentication, ' +
                'invalid organization "%s".' % server.name)
            return False
        user = org.get_user(user_id)
        if not user:
            LogEntry(message='User failed authentication, ' +
                'invalid user "%s".' % server.name)
            return False
        if not user.verify_otp_code(otp_code):
            LogEntry(message='User failed two-step authentication "%s".' % (
                user.name))
            return False
        return True

    def client_connect(self, org_id, user_id):
        org = self.get_org(org_id)
        if not org:
            LogEntry(message='User failed authentication, ' +
                'invalid organization "%s".' % server.name)
            return
        user = org.get_user(user_id)
        if not user:
            LogEntry(message='User failed authentication, ' +
                'invalid user "%s".' % server.name)
            return
        return

    def client_disconnect(self, org_id, user_id):
        org = self.get_org(org_id)
        if not org:
            LogEntry(message='User failed authentication, ' +
                'invalid organization "%s".' % server.name)
            return
        user = org.get_user(user_id)
        if not user:
            LogEntry(message='User failed authentication, ' +
                'invalid user "%s".' % server.name)
            return
        return

    def update_clients(self, clients):
        client_count = len(self.clients)
        self.clients = clients
        if self.status and client_count != len(clients):
            for org in self.iter_orgs():
                Event(type=USERS_UPDATED, resource_id=org.id)
            Event(type=SERVERS_UPDATED)

    def _generate_ovpn_conf(self):
        if not self.org_count:
            raise ServerMissingOrg('Ovpn conf cannot be generated without ' + \
                'any organizations', {
                    'server_id': self.id,
                })

        logger.debug('Generating node server ovpn conf. %r' % {
            'server_id': self.id,
        })

        if not self.primary_organization or not self.primary_user:
            self._create_primary_user()

        if not os.path.isfile(self.dh_param_path):
            self._generate_dh_param()

        primary_org = Organization.get_org(id=self.primary_organization)
        primary_user = primary_org.get_user(self.primary_user)

        self.generate_ca_cert()

        if self.local_networks:
            push = ''
            for network in self.local_networks:
                push += 'push "route %s %s"\n' % self._parse_network(network)
            push = push.rstrip()
        else:
            push = 'push "redirect-gateway"'

        server_conf = OVPN_INLINE_SERVER_CONF % (
            self.port,
            self.protocol,
            self.interface,
            '%s',
            '%s',
            '%s',
            '%s %s' % self._parse_network(self.network),
            '%s',
            push,
            '%s',
            4 if self.debug else 1,
            8 if self.debug else 3,
        )

        if self.otp_auth:
            server_conf += 'auth-user-pass-verify ' + \
                '<%= user_pass_verify_path %> via-file\n'

        if self.lzo_compression:
            server_conf += 'comp-lzo\npush "comp-lzo"\n'

        if self.local_networks:
            server_conf += 'client-to-client\n'

        server_conf += '<ca>\n%s\n</ca>\n' % utils.get_cert_block(
            self.ca_cert_path)
        server_conf += '<cert>\n%s\n</cert>\n' % utils.get_cert_block(
            primary_user.cert_path)
        server_conf += '<key>\n%s\n</key>\n' % open(
            primary_user.key_path).read().strip()
        server_conf += '<dh>\n%s\n</dh>\n' % open(
            self.dh_param_path).read().strip()

        return server_conf

    def start(self, silent=False):
        if self.status:
            return
        if not self.org_count:
            raise ServerMissingOrg('Server cannot be started without ' + \
                'any organizations', {
                    'server_id': self.id,
                })

        logger.debug('Starting node server. %r' % {
            'server_id': self.id,
        })
        ovpn_conf = self._generate_ovpn_conf()

        try:
            response = self._request('post', json_data={
                'network': self.network,
                'local_networks': self.local_networks,
                'ovpn_conf': ovpn_conf,
                'server_ver': NODE_SERVER_VER,
            })
        except:
            raise NodeConnectionError('Failed to connect to node server', {
                'server_id': self.id,
            })

        if response.status_code == 401:
            raise InvalidNodeAPIKey('Invalid node server api key', {
                'server_id': self.id,
                'status_code': response.status_code,
                'reason': response.reason,
            })
        elif response.status_code != 200:
            raise ServerStartError('Failed to start node server', {
                'server_id': self.id,
                'status_code': response.status_code,
                'reason': response.reason,
            })

        self._interrupt = False
        cache_db.dict_set(self.get_cache_key(), 'start_time',
            str(int(time.time() - 1)))
        self.clear_output()
        threading.Thread(target=self._com_thread).start()
        self.status = True

        if not silent:
            Event(type=SERVERS_UPDATED)
            LogEntry(message='Started server "%s".' % self.name)

    def stop(self, silent=False):
        if not self.status:
            return
        self._interrupt = True

        try:
            response = self._request('delete')
        except:
            raise NodeConnectionError('Failed to connect to node server', {
                'server_id': self.id,
            })

        if response.status_code != 200:
            raise ServerStopError('Failed to stop node server', {
                'server_id': self.id,
                'status_code': response.status_code,
                'reason': response.reason,
            })
        self.status = False

        if not silent:
            Event(type=SERVERS_UPDATED)
            LogEntry(message='Stopped server "%s".' % self.name)

    def force_stop(self, silent=False):
        self.stop(silent)
