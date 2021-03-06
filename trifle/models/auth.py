# -*- encoding: utf-8 -*-
from collections import defaultdict
from gi.repository import Gio
from gi.repository import GLib
from gi.repository import GObject
from gi.repository import Secret
from gi.repository import SecretUnstable as SU
from gi.repository import Soup

from trifle.models import settings
from trifle.utils import (logger, connect_once, session, api_method,
                          AuthMessage, Message)


class Keyring(GObject.Object):
    __gsignals__ = {
        'ask-password': (GObject.SignalFlags.ACTION, None, []),
        'password-loaded': (GObject.SignalFlags.RUN_LAST, None, [])
    }
    username = GObject.property(type=str)
    password = GObject.property(type=str)

    def __init__(self, *args, **kwargs):
        super(Keyring, self).__init__(*args, **kwargs)
        settings.settings.bind('username', self, 'username',
                               Gio.SettingsBindFlags.DEFAULT)

    @property
    def has_credentials(self):
        return bool(self.username) and bool(self.password)

    def load_credentials(self):
        if self.has_credentials:
            GLib.idle_add(self.emit, 'password-loaded')
        else:
            GLib.idle_add(self.emit, 'ask-password')

    def credentials_response(self, username, password):
        if username is not None and password is not None:
            self.set_credentials(username, password)
        GLib.idle_add(self.emit, 'password-loaded')

    def set_credentials(self, username, password):
        self.set_properties(username=username, password=password)
        return True


SCHEMA = Secret.Schema.new('apps.trifle', Secret.SchemaFlags.NONE, {
                              'app': Secret.SchemaAttributeType.STRING,
                              'user': Secret.SchemaAttributeType.STRING})

def try_unlock(func):
    """
    Will try to unlock default collection where passwords are supposed to
    be, as it is not necesarrily unlocked when we query the database.
    Async!
    """
    def load_collection(s, res, data):
        data['service'] = s.get_finish(res)
        SU.Collection.for_alias(data['service'], 'default',
                                SU.CollectionFlags.NONE, None,
                                unlock_collection, data)

    def unlock_collection(col, res, data):
        SU.Service.unlock(data['service'],
                          [SU.Collection.for_alias_finish(res)], None,
                          lambda *x: func(*data['args'], **data['kwargs']),
                          None)

    def get_service(*args, **kwargs):
        data = {'args': args, 'kwargs': kwargs}
        SU.Service.get(SU.ServiceFlags.NONE, None, load_collection, data)

    return get_service


class SecretKeyring(Keyring):
    @try_unlock
    def load_credentials(self):

        def loaded(source, result, data):
            password = Secret.password_lookup_finish(result)
            if password is None:
                # We fallback to session storage
                return super(SecretKeyring, self).load_credentials()
            else:
                self.password = password
                GLib.idle_add(self.emit, 'password-loaded')

        if self.has_credentials:
            GLib.idle_add(self.emit, 'password-loaded')
        else:
            attrs = {'app': 'trifle', 'user': self.username}
            Secret.password_lookup(SCHEMA, attrs, None, loaded, None)

    @try_unlock
    def set_credentials(self, username, password):
        def stored(source, result, data):
            if not Secret.password_store_finish(result):
                logger.error('Could not store password into keyring')

        attrs = {'app': 'trifle', 'user': username}
        Secret.password_store(SCHEMA, attrs, None, 'Trifle password',
                              password, None, stored, None)

        super(SecretKeyring, self).set_credentials(username, password)
        return True


class Auth(GObject.Object):
    status = GObject.property(type=object)
    login_token = GObject.property(type=str)
    edit_token = GObject.property(type=str)
    edit_token_expire = GObject.property(type=GObject.TYPE_UINT64)
    keyring = GObject.property(type=Keyring)

    def __init__(self, *args, **kwargs):
        super(Auth, self).__init__(*args, **kwargs)
        self.edit_token_expire = 0
        self.status = defaultdict(bool)

    def message(self, *args, **kwargs):
        return AuthMessage(self, *args, **kwargs)

    def login(self):
        self.status.update({'PROGRESS': True, 'ABORTED': False,
                            'BAD_CREDENTIALS': False, 'OK': False})
        self.notify('status')
        # connect be above, because password-loaded can be emmited during The
        # call and we won't get it.
        connect_once(self.keyring, 'password-loaded',
                     lambda *x: self.on_credentials())
        self.keyring.load_credentials()

    def on_credentials(self):
        if not self.keyring.has_credentials:
            self.status.update({'ABORTED': True, 'PROGRESS': False})
            self.notify('status')
            return

        uri = 'https://www.google.com/accounts/ClientLogin'
        data = 'service=reader&accountType=GOOGLE&Email={0}&Passwd={1}'
        data = data.format(self.keyring.username, self.keyring.password)
        message = Message('POST', uri)
        req_type = 'application/x-www-form-urlencoded'
        message.set_request(req_type, Soup.MemoryUse.COPY, data, len(data))
        session.queue_message(message, self.on_login, None)

    def on_login(self, session, message, data):
        status = message.status_code
        if not 200 <= status < 400:
            logger.error('Authentication failed (HTTP {0})'.format(status))
            self.status.update({'OK': False, 'PROGRESS': False,
                                'BAD_CREDENTIALS': status == 403})
            self.notify('status')
        else: # Login was likely successful
            for line in message.response_body.data.splitlines():
                if line.startswith('Auth'):
                    self.login_token = line[5:]
                    message = self.message('GET', api_method('token'))
                    session.queue_message(message, self.on_token, None)
                    break

    def on_token(self, session, message, data=None):
        status = message.status_code
        if not 200 <= status < 400:
            logger.error('Token request failed (HTTP {0})'.format(status))
        else:
            self.edit_token = message.response_body.data
            self.edit_token_expire = int(GLib.get_real_time() + 1.5E9) #µs
        self.status.update({'PROGRESS': False, 'OK': 200 <= status < 400})
        self.notify('status')

    def token_valid(self):
        return GLib.get_real_time() < self.edit_token_expire
