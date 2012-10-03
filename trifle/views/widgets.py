# -*- coding: utf-8 -*-
from gi.repository import Gtk, WebKit, Pango, PangoCairo, Gdk, GObject, Gio
import datetime

import models
from utils import get_data_path
from views import utils
from models.utils import escape


def add_toolbar_items(toolbar, tb_type):
    stock_toolbutton = Gtk.ToolButton.new_from_stock
    if tb_type == 'items-toolbar':
        toolbar.mark_all = stock_toolbutton(Gtk.STOCK_APPLY)
        toolbar.insert(toolbar.mark_all, -1)

        # toolbar.search = ToolbarSearch(margin_left=5, halign=Gtk.Align.FILL)
        # toolbar.search.set_expand(True)
        # toolbar.insert(toolbar.search, -1)

    elif tb_type == 'sidebar-toolbar':
        toolbar.refresh = stock_toolbutton(Gtk.STOCK_REFRESH)
        toolbar.refresh.set_properties(margin_right=5)
        toolbar.insert(toolbar.refresh, -1)

        toolbar.spinner = ToolbarSpinner(no_show_all=True)
        toolbar.insert(toolbar.spinner, -1)

        toolbar.subscribe = stock_toolbutton(Gtk.STOCK_ADD)
        toolbar.subscribe.set_expand(True)
        toolbar.subscribe.set_halign(Gtk.Align.END)
        toolbar.insert(toolbar.subscribe, -1)

    elif tb_type == 'feedview-toolbar':
        # toolbar.star = stock_toolbutton(Gtk.STOCK_YES)
        # toolbar.star.set_properties(margin_right=5)
        # toolbar.insert(toolbar.star, -1)

        # toolbar.share = stock_toolbutton(Gtk.STOCK_REDO)
        # toolbar.share.set_properties(margin_right=5)
        # toolbar.insert(toolbar.share, -1)

        toolbar.preferences = stock_toolbutton(Gtk.STOCK_PREFERENCES)
        toolbar.preferences.set_halign(Gtk.Align.END)
        toolbar.preferences.set_expand(True)
        toolbar.insert(toolbar.preferences, -1)
    else:
        raise ValueError('Unknown Toolbar')
    toolbar.show_all()


class ToolbarSearch(Gtk.ToolItem):

    def __init__(self, *args, **kwargs):
        super(ToolbarSearch, self).__init__(*args, **kwargs)
        self.entry = Gtk.Entry(hexpand=True, halign=Gtk.Align.FILL)
        self.set_unread_count(0)
        self.add(self.entry)

    def set_unread_count(self, items):
        self.entry.set_placeholder_text(_('Search {0} items').format(items))


class ToolbarSpinner(Gtk.ToolItem):

    def __init__(self, *args, **kwargs):
        super(ToolbarSpinner, self).__init__(*args, **kwargs)
        self.spinner = Gtk.Spinner(active=True)
        self.add(self.spinner)

    def show(self):
        self.spinner.show_all()
        super(ToolbarSpinner, self).show()


class FeedView(WebKit.WebView):

    def __init__(self, *args, **kwargs):
        # TODO: Change to DOCUMENT_VIEWER after we start caching remote
        # resources at item processing stage
        WebKit.set_cache_model(WebKit.CacheModel.DOCUMENT_BROWSER)
        WebKit.get_default_session().set_property('max-conns-per-host', 8)

        super(FeedView, self).__init__(*args, **kwargs)
        self.connect('navigation-policy-decision-requested', self.on_navigate)
        self.connect('console-message', self.on_console_message)

        self.settings = WebKit.WebSettings()
        stylesheet_path = get_data_path('ui', 'feedview', 'style.css')
        self.settings.set_properties(
            # These three saves us ~25MiB of residental memory
            enable_scripts=False, enable_plugins=False,
            enable_java_applet=False,
            # We already have most files cached and load locally
            enable_page_cache=False, enable_dns_prefetching=False,
            # Need this one of usability reasons.
            enable_default_context_menu=False,
            # Not used
            enable_html5_database=False, enable_html5_local_storage=False,
            enable_offline_web_application_cache=False,
            enable_xss_auditor=False, resizable_text_areas=False,
            # Very effectively turns off all types of cache
            enable_private_browsing=True,
            user_stylesheet_uri='file://' + stylesheet_path
        )
        self.set_settings(self.settings)
        self.load_item()

    @staticmethod
    def on_navigate(self, frame, request, action, policy):
        uri = action.get_original_uri()
        if frame.get_parent():
            logger.warning('{0} was not loaded'.format(uri))
            policy.ignore()
            return True
        elif uri.startswith('http'):
            if not Gio.AppInfo.launch_default_for_uri(uri, None):
                logger.error('System could not open {0}'.format(uri))
            policy.ignore()
            return True
        return False

    @staticmethod
    def on_console_message(self, message, line, source):
        logger.debug(message)
        return True

    def load_item(self, item=None):
        with open(get_data_path('ui', 'feedview', 'template.html'), 'r') as f:
            template = f.read()
        if item is None:
            return self.load_html_string('', 'file://')
        else:
            content = item.read_content(item.item_id)
            dt = datetime.datetime.fromtimestamp(item.time)
            s = template.format(title=item.title, content=content,
                                href=item.href, author=item.author,
                                datetime=dt)
            return self.load_html_string(s, 'file://')

    def on_change(self, treeview):
        if treeview.in_destruction() or treeview.reloading:
            return
        selection = treeview.get_selection().get_selected()
        item = selection[0].get_value(selection[1], 0)
        if item.unread: # Set it to read
            treeview.store.set_read(item)
        self.load_item(item)


class CategoriesView(Gtk.TreeView):

    def __init__(self, *args, **kwargs):
        self._store = Gtk.ListStore(str, str, str)
        super(CategoriesView, self).__init__(self._store, *args, **kwargs)
        self.set_properties(headers_visible=False)

        column = Gtk.TreeViewColumn("Categories")
        icon = Gtk.CellRendererPixbuf()
        title = Gtk.CellRendererText()
        column.pack_start(icon, False)
        column.pack_start(title, True)
        column.add_attribute(icon, "icon-name", 0)
        column.add_attribute(title, "text", 1)
        self.append_column(column)

        self.selection = self.get_selection()
        i = self.append(Gtk.STOCK_JUSTIFY_FILL, _('All items'), 'reading-list')
        self.append(Gtk.STOCK_INDEX, _('Unread'), 'unread')
        self.append(Gtk.STOCK_ABOUT, _('Starred'), 'starred')
        self.selection.select_iter(i)

    def append(self, icon, title, tp):
        return self._store.append((icon, title, tp,))


class SubscriptionsView(Gtk.TreeView):

    def __init__(self, *args, **kwargs):
        self.store = models.subscriptions.Subscriptions()
        super(SubscriptionsView, self).__init__(self.store, *args, **kwargs)
        self.set_properties(headers_visible=False)
        self.set_level_indentation(-12)

        column = Gtk.TreeViewColumn("Subscription")
        icon_renderer = Gtk.CellRendererPixbuf()
        title_renderer = Gtk.CellRendererText(ellipsize_set=True,
                                            ellipsize=Pango.EllipsizeMode.END)
        column.pack_start(icon_renderer, False)
        column.pack_start(title_renderer, True)
        column.add_attribute(icon_renderer, 'pixbuf', 2)
        column.add_attribute(title_renderer, 'text', 3)
        self.append_column(column)

        self.connect('realize', self.on_realize)

    @staticmethod
    def on_realize(self):
        self.store.update()

    def on_cat_change(self, treeview):
        if treeview.in_destruction():
            return
        self.get_selection().unselect_all()

    def sync(self, callback=None):
        logger.debug('Starting subscriptions\' sync')
        self.store.sync()
        if callback is not None:
            utils.connect_once(self.store, 'sync-done', callback)


class ItemsView(Gtk.TreeView):
    def __init__(self, *args, **kwargs):
        self.reloading = False
        self.store = models.feeds.FilteredItems()
        super(ItemsView, self).__init__(self.store, *args, **kwargs)
        self.set_properties(headers_visible=False)

        renderer = ItemCellRenderer()
        column = Gtk.TreeViewColumn("Item", renderer, item=0)
        self.append_column(column)
        self.connect('realize', self.on_realize)

    def sync(self, callback=None):
        logger.debug('Starting items\' sync')
        self.store.sync()
        if callback is not None:
            utils.connect_once(self.store, 'sync-done', callback)

    @staticmethod
    def on_realize(self):
        self.store.set_category('reading-list')

    def on_filter_change(self, treeview):
        if treeview.in_destruction():
            return
        model, selection = treeview.get_selection().get_selected()
        self.reloading = True
        if selection is not None:
            row = model[selection]
            self.store.set_filter(row[0], row[1])
        else:
            logger.warning('Cannot set filter, there\'s no selection')
        self.reloading = False

    def on_cat_change(self, treeview):
        if treeview.in_destruction():
            return
        model, selection = treeview.get_selection().get_selected()
        self.reloading = True
        if selection is not None:
            self.store.set_category(model[selection][2])
        self.reloading = False


class ItemCellRenderer(Gtk.CellRenderer):
    item = GObject.property(type=models.feeds.FeedItem)
    markup = {'date': u('<span color="{color}" size="9216">{text}</span>'),
              'site': u('<span color="{color}" size="9216">{text}</span>'),
              'title': u('<span color="{color}" size="10240" '
                       'weight="{weight}">{text}</span>'),
              'summary': u('<span color="{color}" size="9216">{text}</span>'),
              'dummy': u('<span size="{size}">{text}</span>')}
    height = None
    padding = 2
    line_spacing = 2
    icon_size = 16
    sizes = {'date': 9216, 'site': 9216, 'title': 10240, 'summary': 9216}
    heights = [0, 0, 0]

    def __init__(self, *args, **kwargs):
        super(ItemCellRenderer, self).__init__(*args, **kwargs)
        self.left_padding = 0 # Replaced later by render_icon
        self.state = Gtk.StateFlags.FOCUSED

    def do_get_preferred_height(self, view):
        if self.height is None:
            layout = view.create_pango_layout('Gg')
            mapping = {'size': max(self.sizes['date'], self.sizes['site']),
                       'text': 'Gg'}
            layout.set_markup(self.markup['dummy'].format_map(mapping))
            self.heights[0] = max(self.icon_size,
                                  layout.get_pixel_extents()[1].height)
            mapping['size'] = self.sizes['title']
            layout.set_markup(self.markup['dummy'].format_map(mapping))
            self.heights[1] = layout.get_pixel_extents()[1].height
            mapping['size'] = self.sizes['summary']
            layout.set_markup(self.markup['dummy'].format_map(mapping))
            self.heights[2] = layout.get_pixel_extents()[1].height
            self.heights = [h + self.line_spacing for h in self.heights]
            ItemCellRenderer.height = self.padding * 2 + sum(self.heights)
        return self.height, self.height

    # Any of render functions should not modify self.* in any way
    def do_render(self, context, view, bg_area, cell_area, flags):
        if flags & Gtk.CellRendererState.FOCUSED:
            self.state = Gtk.StateFlags.SELECTED
        else:
            self.state = Gtk.StateFlags.NORMAL

        y, x = cell_area.y + self.padding, cell_area.x + self.padding
        style = view.get_style_context()

        # First line containing icon, subscription title and date
        icon_w, icon_h = self.render_icon(y, x, context)
        date_w, date_h = self.render_date(y, icon_h, view, context, cell_area,
                                          style)
        site_w = cell_area.width - date_w - self.line_spacing * 2 - icon_w - x
        self.render_site(y, x + icon_w + self.line_spacing, site_w, icon_h,
                         view, context, cell_area, style)

        # This  is width for both title and summary
        ts_w = cell_area.width - self.padding * 2
        # Second line, title of item
        y += self.line_spacing + self.heights[1]
        title_w, title_h = self.render_title(y, x, ts_w, view, context, style)

        # Third line, summary
        y += self.line_spacing + self.heights[2]
        summ_w, summ_h = self.render_summary(y, x, ts_w, view, context, style)

    def render_icon(self, y, x, context=None):
        if self.item is None:
            return 16, 16 # Icons will always be 16x16 (But there may be none)

        icon = self.item.icon
        if icon is not None:
            Gdk.cairo_set_source_pixbuf(context, icon, x, y)
            context.paint()
            return icon.get_width(), icon.get_height()
        return 0, 0

    def render_date(self, y, icon_h, view, context, cell_area, style):
        if self.item is None:
            return 0, 0

        # We want to use theme colors for time string. So in Adwaita text
        # looks blue, and in Ubuntu default theme – orange.
        if self.state == Gtk.StateFlags.NORMAL:
            color = style.get_background_color(Gtk.StateFlags.SELECTED)
        else:
            color = style.get_color(Gtk.StateFlags.SELECTED)

        text = utils.time_ago(self.item.time)
        markup = self.markup['date'].format(text=text,
                                            color=utils.hexcolor(color))

        layout = view.create_pango_layout(text)
        layout.set_markup(markup)
        layout.set_alignment(Pango.Alignment.RIGHT)

        rect = layout.get_pixel_extents()[1]
        y += (icon_h - rect.height) / 2
        x = cell_area.width - rect.width - rect.x - self.padding
        context.move_to(x, y)
        PangoCairo.show_layout(context, layout)
        return rect.width, rect.height

    def render_site(self, y, x, width, icon_h, view, context, cell_area,
                    style):
        if self.item is None:
            return 0, 0

        color = utils.hexcolor(style.get_color(self.state))
        text = self.item.site
        markup = self.markup['site'].format(text=escape(text), color=color)

        layout = view.create_pango_layout(text)
        layout.set_markup(markup)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        layout.set_width(width * Pango.SCALE)
        rect = layout.get_pixel_extents()[1]
        y += (icon_h - rect.height) / 2
        context.move_to(x, y)
        PangoCairo.show_layout(context, layout)
        return rect.width, rect.height

    def render_title(self, y, x, width, view, context, style):
        if self.item is None:
            return 0, 0

        text = self.item.title
        weight = 'bold' if self.item.unread else 'normal'
        color = utils.hexcolor(style.get_color(self.state))
        markup = self.markup['title'].format(text=escape(text), color=color,
                                             weight=weight)

        layout = view.create_pango_layout(text)
        layout.set_markup(markup)
        layout.set_wrap(Pango.WrapMode.WORD)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        layout.set_width(width * Pango.SCALE)
        context.move_to(x, y)
        PangoCairo.show_layout(context, layout)
        rect = layout.get_pixel_extents()[1]
        return rect.width, rect.height

    def render_summary(self, y, x, width, view, context, style):
        if self.item is None:
            return 0, 0

        text = self.item.summary
        color = utils.hexcolor(style.get_color(self.state))
        markup = self.markup['summary'].format(text=escape(text), color=color)

        layout = view.create_pango_layout(text)
        layout.set_markup(markup)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        layout.set_width(width * Pango.SCALE)
        rect = layout.get_pixel_extents()[1]
        context.move_to(x, y)
        PangoCairo.show_layout(context, layout)
        return rect.width, rect.height
