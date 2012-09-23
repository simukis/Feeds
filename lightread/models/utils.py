from gi.repository import Soup, Gtk, GdkPixbuf
from html.parser import HTMLParser
from urllib import parse
import hashlib
import itertools
import os
import sqlite3

from lightread.utils import get_data_path

if 'cacher_session' not in _globals_cache:
    _globals_cache['models_session'] = Soup.SessionAsync(max_conns=50,
                                                         max_conns_per_host=8)
session = _globals_cache['models_session']

if 'sqlite_cnn' not in _globals_cache:
    fpath = os.path.join(CACHE_DIR, 'metadata')
    if not os.path.exists(fpath):
        connection = sqlite3.Connection(fpath)
        with open(get_data_path('db_init.sql'), 'r') as script:
            connection.executescript(script.read())
    else:
        connection = sqlite3.Connection(fpath)
    _globals_cache['sqlite_cnn'] = connection
connection = _globals_cache['sqlite_cnn']


class Message(Soup.Message):
    def __new__(cls, *args, **kwargs):
        obj = Soup.Message.new(*args, **kwargs)
        hdr = obj.get_property('request-headers')
        hdr.append('User-Agent', 'LightRead/dev')
        return obj


class AuthMessage(Message):
    """
    Creates an Soup.Message object with GoogleLogin headers injected
    """
    def __new__(cls, auth, *args, **kwargs):
        obj = super().__new__(cls, *args, **kwargs)
        hdr = obj.get_property('request-headers')
        hdr.append('Authorization', 'GoogleLogin auth={0}'.format(auth.key))
        return obj


class LoginRequired:
    """Injects ensure_login method which will make sure, that method is
    executed when person is logged in.
    """
    def ensure_login(self, auth, func, *args, **kwargs):
        """
        If auth object has no key, this function will return False, ask
        Auth object to get one and then call func with *args and **kwargs
        """
        if not auth.key:
            logger.debug('auth object has no key, asking to get one')
            def status_change(auth):
                return func(*args, **kwargs)
            auth.login()
            auth.connect('status-change', status_change)
            return False
        return True


def api_method(path, getargs=None):
    if getargs is None:
        getargs = []
    base = 'https://www.google.com/reader/api/0/'
    # Is it dict?
    try:
        getargs = getargs.items()
    except AttributeError:
        pass
    # Will not override earlier output variable
    getargs = getargs + [('output', 'json')]
    return "{0}?{1}".format(parse.urljoin(base, path), parse.urlencode(getargs))


def icon_name(origin_url):
    fname = hashlib.md5(bytes(origin_url, 'utf-8')).hexdigest()
    return os.path.join(CACHE_DIR, 'favicons', fname)


def icon_pixbuf(url):
    """Load cached icon pixbuf from url. Will try to find a suitable fallback
    if nothing found
    """
    fpath = icon_name(url)
    if not os.path.isfile(fpath):
        selections = ['image-loading']
    elif os.path.getsize(fpath) > 10:
        return GdkPixbuf.Pixbuf.new_from_file_at_size(fpath, 16, 16)
    else:
        selections = ['application-rss+xml', 'application-atom+xml',
                      'text-html', Gtk.STOCK_FILE]

    icon_theme = Gtk.IconTheme.get_default()
    icon_flag = Gtk.IconLookupFlags.GENERIC_FALLBACK
    icon = icon_theme.choose_icon(selections, 16, icon_flag)

    if icon is None:
        return None
    else:
        return icon.load_icon()

def split_chunks(itr, chunk_size, fillvalue=None):
    items = [iter(itr)] * chunk_size
    return itertools.zip_longest(*items, fillvalue=fillvalue)

unescape = HTMLParser().unescape
