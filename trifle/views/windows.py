from gi.repository import Gtk

from trifle import models
from trifle.utils import VERSION, _, logger
from trifle.views import widgets, utils


class ApplicationWindow(utils.BuiltMixin, Gtk.ApplicationWindow):
    ui_file = 'window.ui'
    top_object = 'main-window'

    def __init__(self, *args, **kwargs):
        Gtk.ApplicationWindow.__init__(self, *args, **kwargs)
        self.set_wmclass('Trifle', 'Trifle')
        self.maximize() # Shall we do that by default?

        tbr = self.toolbar = widgets.MainToolbar()
        items = self.items = widgets.ItemsView()
        subscrs = self.subscriptions = widgets.SubscriptionsView()
        item = self.item_view = widgets.ItemView()

        base_box = self.builder.get_object('base-box')
        base_box.pack_start(tbr, False, True, 0)
        base_box.reorder_child(tbr, 0)
        self.builder.get_object('subscriptions').add(subscrs)
        self.builder.get_object('items').add(items)
        self.builder.get_object('item').add(item)
        subscrs.show()
        items.show()
        item.show()
        items.store.category = 'reading-list'

        subscrs.connect('cursor-changed', items.on_filter_change)
        items.connect('cursor-changed', item.on_change)
        tbr.connect('notify::category', lambda t, p: items.on_cat_change(t))
        tbr.connect('notify::category', lambda t, p: subscrs.on_cat_change(t))
        tbr.starred.connect('toggled', item.on_star)
        tbr.unread.connect('toggled', item.on_keep_unread)
        item.connect('notify::item', lambda i, x: tbr.set_item(i.item))

# TODO: These doesn't work correctly yet.
#     def on_horiz_pos_change(self, paned, gprop):
#         paned = self.builder.get_object('paned')
#         models.settings.settings['horizontal-pos'] = paned.props.position
#
#     def on_vert_pos_change(self, paned, gprop):
#         paned_side = self.builder.get_object('paned-side')
#         models.settings.settings['vertical-pos'] = paned_side.props.position



class PreferencesDialog(utils.BuiltMixin, Gtk.Dialog):
    ui_file = 'preferences-dialog.ui'
    top_object = 'preferences-dialog'

    def __init__(self, *args, **kwargs):
        self.set_properties(**kwargs)
        self.connect('response', self.on_response)

        for cb_name in ['notifications', 'start-refresh']:
            checkbox = self.builder.get_object(cb_name)
            checkbox.set_active(models.settings.settings[cb_name])
            checkbox.connect('toggled', self.on_toggle, cb_name)

        refresh = self.builder.get_object('refresh-every')
        for time, label in ((0, _('Never')), (5, _('5 minutes')),
                            (10, _('10 minutes')), (30, _('30 minutes')),
                            (60, _('1 hour'))):
            refresh.append(str(time), label)
        refresh.set_active_id(str(models.settings.settings['refresh-every']))
        refresh.connect('changed', self.on_change, 'refresh-every')

        adjustment = self.builder.get_object('cache-upto-value')
        adjustment.set_value(models.settings.settings['cache-items'])
        adjustment.connect('value-changed', self.on_val_change, 'cache-items')

    def on_change(self, widget, setting):
        models.settings.settings[setting] = int(widget.get_active_id())

    def on_val_change(self, adj, setting):
        models.settings.settings[setting] = adj.get_value()

    def on_toggle(self, widget, setting):
        models.settings.settings[setting] = widget.get_active()

    def on_response(self, dialog, r):
        if r in (Gtk.ResponseType.DELETE_EVENT, Gtk.ResponseType.OK):
            self.destroy()


class AboutDialog(utils.BuiltMixin, Gtk.AboutDialog):
    ui_file = 'about-dialog.ui'
    top_object = 'about-dialog'

    def __init__(self, *args, **kwargs):
        self.set_properties(version=VERSION, **kwargs)


class LoginDialog(utils.BuiltMixin, Gtk.Dialog):
    """
    This dialog will ensure, that user becomes logged in by any means
    """
    ui_file = 'login-dialog.ui'
    top_object = 'login-dialog'

    def __init__(self, *args, **kwargs):
        self.set_properties(**kwargs)

        self.user_entry = self.builder.get_object('username')
        self.passwd_entry = self.builder.get_object('password')
        self.msg = Gtk.Label()
        self.builder.get_object('message').pack_start(self.msg, False, True, 0)
        self.user_entry.connect('activate', self.on_activate)
        self.passwd_entry.connect('activate', self.on_activate)
        self.connect('response', self.on_response)

    def on_response(self, dialog, r, data=None):
        if r in (Gtk.ResponseType.DELETE_EVENT, Gtk.ResponseType.CANCEL):
            # <ESC> or [Cancel] button pressed
            self.destroy()
            return
        user = self.builder.get_object('username').get_text()
        password = self.builder.get_object('password').get_text()
        logger.debug('username={0} and passwd is {1} chr long'.format(
                     user, len(password)))
        if len(password) == 0 or len(user) == 0:
            self.msg.set_text(_('All fields are required'))
            return False
        models.auth.keyring.set_credentials(user, password)
        self.destroy()

    def on_activate(self, entry, data=None):
        self.emit('response', 0)


class SubscribeDialog(utils.BuiltMixin, Gtk.Dialog):
    """
    This dialog will ensure, that user becomes logged in by any means
    """
    ui_file = 'subscribe-dialog.ui'
    top_object = 'subscribe-dialog'

    def __init__(self, *args, **kwargs):
        self.set_properties(**kwargs)

        self.url_entry = self.builder.get_object('url')
        self.url_entry.connect('activate', self.on_activate)
        self.url = None
        self.connect('response', self.on_response)

    def on_response(self, dialog, r, data=None):
        if r in (Gtk.ResponseType.DELETE_EVENT, Gtk.ResponseType.CANCEL):
            # <ESC> or [Cancel] button pressed
            self.destroy()
            return
        url = self.url_entry.get_text()
        if len(url) == 0:
            return
        logger.debug('Subscribing to {0}'.format(url))
        self.url = url
        self.destroy()

    def on_activate(self, entry, data=None):
        self.emit('response', 0)
