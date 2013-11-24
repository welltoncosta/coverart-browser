# -*- Mode: python; coding: utf-8; tab-width: 4; indent-tabs-mode: nil; -*-
#
# Copyright (C) 2012 - fossfreedom
# Copyright (C) 2012 - Agustin Carrasco
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2, or (at your option)
# any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA 02110-1301  USA.

from coverart_external_plugins import CreateExternalPluginMenu
from gi.repository import Gdk
from gi.repository import Gtk
from gi.repository import GLib
from gi.repository import GObject
from gi.repository import Gio
from gi.repository import GdkPixbuf
from gi.repository import RB

from coverart_browser_prefs import GSetting
from coverart_album import Cover
from coverart_album import Album
from coverart_album import AlbumsModel
from coverart_album import CoverManager
from coverart_widgets import AbstractView
from coverart_utils import SortedCollection
from coverart_widgets import PanedCollapsible
from coverart_toolbar import ToolbarObject
from coverart_utils import idle_iterator
from coverart_utils import dumpstack
from coverart_utils import create_pixbuf_from_file_at_size
from coverart_external_plugins import CreateExternalPluginMenu
import coverart_rb3compat as rb3compat 

import rb
import os

from collections import namedtuple

import tempfile, shutil
def create_temporary_copy(path):
    temp_dir = tempfile.gettempdir()
    filename = tempfile.mktemp()
    temp_path = os.path.join(temp_dir, filename)
    shutil.copy2(path, temp_path)
    return temp_path

ARTIST_LOAD_CHUNK = 50

class Artist(GObject.Object):
    '''
    An album. It's conformed from one or more tracks, and many of it's
    information is deduced from them.

    :param name: `str` name of the artist.
    :param cover: `Cover` cover for this artist.
    '''
    # signals
    __gsignals__ = {
        'modified': (GObject.SIGNAL_RUN_FIRST, None, ()),
        'emptied': (GObject.SIGNAL_RUN_LAST, None, ()),
        'cover-updated': (GObject.SIGNAL_RUN_LAST, None, ())
        }

    __hash__ = GObject.__hash__
    
    def __init__(self, name, cover):
        super(Artist, self).__init__()

        self.name = name
        self._cover = None
        self.cover = cover

        self._signals_id = {}

    @property
    def cover(self):
        return self._cover

    @cover.setter
    def cover(self, new_cover):
        #if self._cover:
        #    self._cover.disconnect(self._cover_resized_id)

        self._cover = new_cover
        #self._cover_resized_id = self._cover.connect('resized',
        #    lambda *args: self.emit('cover-updated'))

        self.emit('cover-updated')
    def create_ext_db_key(self):
        '''
        Returns an `RB.ExtDBKey` 
        '''
        return RB.ExtDBKey.create_lookup('artist', self.name)


class ArtistsModel(GObject.Object):
    '''
    Model that contains artists, keeps them sorted, filtered and provides an
    external `Gtk.TreeModel` interface to use as part of a Gtk interface.

    The `Gtk.TreeModel` haves the following structure:
    column 0 -> string containing the artist name
    column 1 -> pixbuf of the artist's cover.
    column 2 -> instance of the artist or album itself.
    column 3 -> boolean that indicates if the row should be shown
    '''
    # signals
    __gsignals__ = {
        'update-path': (GObject.SIGNAL_RUN_LAST, None, (object,)),
        'visual-updated': ((GObject.SIGNAL_RUN_LAST, None, (object, object)))
        }

    # list of columns names and positions on the TreeModel
    columns = {'tooltip': 0, 'pixbuf': 1, 'artist_album': 2, 'show': 3, 'empty': 4}

    def __init__(self, album_manager):
        super(ArtistsModel, self).__init__()

        self.album_manager = album_manager
        self._iters = {}
        self._albumiters = {}
        self._artists = SortedCollection(
            key=lambda artist: getattr(artist, 'name'))

        self._tree_store = Gtk.TreeStore(str, GdkPixbuf.Pixbuf, object, 
            bool,  str)
            
        # sorting idle call
        self._sort_process = None

        # create the filtered store that's used with the view
        self._filtered_store = self._tree_store.filter_new()
        self._filtered_store.set_visible_column(ArtistsModel.columns['show'])
        
        self._tree_sort = Gtk.TreeModelSort(model=self._filtered_store)            
        self._tree_sort.set_sort_func(0, self._compare, None)
        
        self._connect_signals()
        
    def _connect_signals(self):
        self.connect('update-path', self._on_update_path)
        self.album_manager.model.connect('filter-changed', self._on_album_filter_changed)
        
    def _on_album_filter_changed(self, *args):
        if len(self._iters) == 0:
            return
            
        artists = list(set(row[AlbumsModel.columns['album']].artist for row in self.album_manager.model.store))

        for artist in self._iters:
            self.show(artist, artist in artists)
        
    def _compare(self, model, row1, row2, user_data):
        sort_column = 0
        
        #if sort_column:
        value1 = RB.search_fold(model.get_value(row1, sort_column))
        value2 = RB.search_fold(model.get_value(row2, sort_column))
        if value1 < value2:
            return -1
        elif value1 == value2:
            return 0
        else:
            return 1
        
    @property
    def store(self):
        #return self._filtered_store
        return self._tree_sort

    def add(self, artist):
        '''
        Add an artist to the model.

        :param artist: `Artist` to be added to the model.
        '''
        # generate necessary values
        values = self._generate_artist_values(artist)
        # insert the values
        pos = self._artists.insert(artist)
        tree_iter = self._tree_store.insert(None,pos, values)
        child_iter = self._tree_store.insert(tree_iter, pos, values) # dummy child row so that the expand is available
        # connect signals
        ids = (artist.connect('modified', self._artist_modified),
            artist.connect('cover-updated', self._cover_updated),
            artist.connect('emptied', self.remove))
        
        if not artist.name in self._iters:
            self._iters[artist.name] = {}
        self._iters[artist.name] = {'artist_album': artist,
            'iter': tree_iter, 'dummy_iter': child_iter}
        return tree_iter
    
    def _emit_signal(self, tree_iter, signal):
        # we get the filtered path and iter since that's what the outside world
        # interacts with
        tree_path = self._filtered_store.convert_child_path_to_path(
            self._tree_store.get_path(tree_iter))

        if tree_path:
            # if there's no path, the album doesn't show on the filtered model
            # so no one needs to know
            tree_iter = self._filtered_store.get_iter(tree_path)

            self.emit(signal, tree_path, tree_iter)
            
    def remove(self, *args):
        print ("artist remove")
        
    def _cover_updated(self, artist):
        print ("artist cover updated")
        tree_iter = self._iters[artist.name]['iter']

        if self._tree_store.iter_is_valid(tree_iter):
            # only update if the iter is valid
            pixbuf = artist.cover.pixbuf

            self._tree_store.set_value(tree_iter, self.columns['pixbuf'],
                pixbuf)

            self._emit_signal(tree_iter, 'visual-updated')
            
    def _artist_modified(self, *args):
        print ("artist modified")
        
        
    def _on_update_path(self, widget, treepath):
        '''
        Add an album to the artist in the model.

        :param artist: `Artist` for the album to be added to (i.e. the parent)
        :param album: `Album` is the child of the Artist
        
        '''
        artist = self.get_from_path(treepath)
        albums = self.album_manager.model.get_all()
        # get the artist iter
        artist_iter = self._iters[artist.name]['iter']
        
        # now remove the dummy_iter - if this fails, we've removed this 
        # before and have no need to add albums
        
        if 'dummy_iter' in self._iters[artist.name]:
            self._iters[artist.name]['album'] = []
            for album in albums:
                if artist.name == album.artist:
                    # generate necessary values
                    values = self._generate_album_values(album)
                    # insert the values
                    tree_iter = self._tree_store.append(artist_iter, values)
                    self._albumiters[album] = {}
                    self._albumiters[album]['iter'] = tree_iter
                    self._albumiters[album]['update-id'] = \
                        album.connect('cover-updated', self._album_coverupdate)
                    
            self._tree_store.remove(self._iters[artist.name]['dummy_iter'])
            del self._iters[artist.name]['dummy_iter']
            
    def _album_coverupdate(self, album):
        tooltip, pixbuf, album, show, blank = self._generate_album_values(album)
        self._tree_store.set_value(self._albumiters[album]['iter'], self.columns['pixbuf'], pixbuf)
            
    def _generate_artist_values(self, artist):
        tooltip = artist.name
        pixbuf = artist.cover.pixbuf #.scale_simple(48,48,GdkPixbuf.InterpType.BILINEAR)
        show = True#self._artist_filter(artist)

        return tooltip, pixbuf, artist, show, ''
    
    def _generate_album_values(self, album):
        tooltip = album.name
        pixbuf = album.cover.pixbuf.scale_simple(48,48,GdkPixbuf.InterpType.BILINEAR)
        show = True

        return tooltip, pixbuf, album, show, ''

    def remove(self, artist):
        '''
        Removes this album from the model.

        :param artist: `Artist` to be removed from the model.
        '''
        self._artists.remove(artist)
        self._tree_store.remove(self._iters[artist.name]['iter'])

        del self._iters[artist.name]

    def contains(self, artist_name):
        '''
        Indicates if the model contains a specific artist.

        :param artist_name: `str` name of the artist.
        '''
        return artist_name in self._iters

    def get(self, artist_name):
        '''
        Returns the requested artist.

        :param artist_name: `str` name of the artist.
        '''
        return self._iters[artist_name]['artist_album']
        
    def get_all(self):
        '''
        Returns a collection of all the artists in this model.
        '''
        return self._artists
        
    def get_from_path(self, path):
        '''
        Returns the Artist or Album referenced by a `Gtk.TreeModel` path.

        :param path: `Gtk.TreePath` referencing the artist.
        '''
        return self._filtered_store[path][self.columns['artist_album']]

    def get_path(self, artist):
        return self._filtered_store.convert_child_path_to_path(
            self._tree_store.get_path(
                self._iters[artist.name]['iter']))
                
    def get_from_ext_db_key(self, key):
        '''
        Returns the requested artist.

        :param key: ext_db_key
        '''
        # get the album name and artist
        name = key.get_field('artist')
        
        # first check if there's a direct match
        artist = self.get(name) if self.contains(name) else None
        
        return artist

    def show(self, artist_name, show):
        '''
        Unfilters an artist, making it visible to the publicly available model's
        `Gtk.TreeModel`

        :param artist: `Artist` to show or hide.
        :param show: `bool` indcating whether to show(True) or hide(False) the
            artist.
        '''
        artist_iter = self._iters[artist_name]['iter']

        if self._tree_store.iter_is_valid(artist_iter):
            self._tree_store.set_value(artist_iter, self.columns['show'], show)

class ArtistCellRenderer(Gtk.CellRendererPixbuf):
    
    def __init__(self):
        super(ArtistCellRenderer, self).__init__()
        
    def do_render(self, cr, widget,  
                background_area,
                cell_area,
                flags):
        
        newpix = self.props.pixbuf #.copy()
        #newpix = newpix.scale_simple(48,48,GdkPixbuf.InterpType.BILINEAR)
        
        Gdk.cairo_set_source_pixbuf(cr, newpix, 0, 0)
        cr.paint()
    
class ArtistLoader(GObject.Object):
    '''
    Loads Artists - updating the model accordingly.

    :param artist_manager: `artist_manager` responsible for this loader.
    '''
    # signals
    __gsignals__ = {
        'artists-load-finished': (GObject.SIGNAL_RUN_LAST, None, (object,)),
        'model-load-finished': (GObject.SIGNAL_RUN_LAST, None, ())
        }

    def __init__(self, artist_manager, album_manager):
        super(ArtistLoader, self).__init__()

        self.shell = artist_manager.shell
        self._connect_signals()
        self._album_manager = album_manager
        self._artist_manager = artist_manager
        
        self.model = artist_manager.model
    
    def load_artists(self):
        albums = self._album_manager.model.get_all()
        model = list(set(album.artist for album in albums))
        
        self._load_artists(iter(model), artists={}, model=model, 
            total=len(model), progress=0.)
            
    @idle_iterator
    def _load_artists(self):
        def process(row, data):
            # allocate the artist
            artist = Artist(row, self._artist_manager.cover_man.unknown_cover)
                
            data['artists'][row] = artist
            
        def after(data):
            # update the progress
            data['progress'] += ARTIST_LOAD_CHUNK

            self._album_manager.progress = data['progress'] / data['total']

        def error(exception):
            print('Error processing entries: ' + str(exception))

        def finish(data):
            self._album_manager.progress = 1
            self.emit('artists-load-finished', data['artists'])

        return ARTIST_LOAD_CHUNK, process, after, error, finish

    @idle_iterator
    def _load_model(self):
        def process(artist, data):
            # add  the artists to the model
            self._artist_manager.model.add(artist)
            
        def after(data):
            data['progress'] += ARTIST_LOAD_CHUNK

            # update the progress
            self._album_manager.progress = 1 - data['progress'] / data['total']
            
        def error(exception):
            dumpstack("Something awful happened!")
            print('Error(2) while adding artists to the model: ' + str(exception))

        def finish(data):
            self._album_manager.progress = 1
            self.emit('model-load-finished')
            #return False

        return ARTIST_LOAD_CHUNK, process, after, error, finish
        
    def _connect_signals(self):
        # connect signals for updating the albums
        #self.entry_changed_id = self._album_manager.db.connect('entry-changed',
        #    self._entry_changed_callback)
        pass
        
    def do_artists_load_finished(self, artists):
        self._load_model(iter(list(artists.values())), total=len(artists), progress=0.)
        
class ArtistCoverManager(CoverManager):
    
    def __init__(self, plugin, artist_manager):
        super(ArtistCoverManager, self).__init__(plugin, artist_manager, 'artist-art')

        self.cover_size = 72

        # create unknown cover and shadow for covers
        self.create_unknown_cover(plugin)
            
    def create_unknown_cover(self, plugin):
        # create the unknown cover
        self.unknown_cover = self.create_cover(
            rb.find_plugin_file(plugin, 'img/microphone.png'))

        super(ArtistCoverManager,self).create_unknown_cover(plugin)
        
    def update_cover(self, coverobject, pixbuf=None, uri=None, update=None):
        '''
        Updates the cover database, inserting the uri as the cover art for
        all the entries on the album.
        
        :param coverobject: `Artist` for which the cover is.
        :param update: bool to say if to update cover with the given uri.
        :param uri: `str` from where we should try to retrieve an image.
        '''

        if update:
            # if it's a pixbuf, assign it to all the artist for the album
            key = RB.ExtDBKey.create_storage('artist', coverobject.name)                    
            self.cover_db.store_uri(key, RB.ExtDBSourceType.USER,uri)
        elif pixbuf:
            # if it's a pixbuf
            
            temp_dir = tempfile.gettempdir()
            filename = tempfile.mktemp()
            temp_path = os.path.join(temp_dir, filename)
            pixbuf.savev(temp_path, 'png', [], []) # WE NEED TO CLEANUP TEMPORARY FILES
            self.update_cover(coverobject, update=True, uri="file://" + temp_path)
            
        elif uri:
            parsed = rb3compat.urlparse(uri)
            
            if parsed.scheme == 'file':
                # local file - assign it
                path = rb3compat.url2pathname(uri.strip()).replace('file://', '')

                if os.path.exists(path):
                    new_temp_file = create_temporary_copy(path)
                    self.update_cover(coverobject, update=True, uri="file://" + new_temp_file)
                    #os.remove(new_temp_file)  WE NEED TO CLEANUP TEMPORARY FILES

            else:
                # assume is a remote uri and we have to retrieve the data
                def cover_update(data, coverobject):
                    # save the cover on a temp file 
                    with tempfile.NamedTemporaryFile(mode='w') as tmp:
                        try:
                            tmp.write(data)
                            tmp.flush()
                            # set the new cover
                            new_temp_file = create_temporary_copy(tmp.name) #WE NEED TO CLEANUP TEMPORARY FILES
                            self.update_cover(coverobject, update=True, uri="file://"+new_temp_file)
                        except:
                            print("The URI doesn't point to an image or " + \
                                "the image couldn't be opened.")

                async = rb.Loader()
                async.get_url(uri, cover_update, coverobject)

class ArtistManager(GObject.Object):
    '''
    Main construction that glues together the different managers, the loader
    and the model. It takes care of initializing all the system.

    :param plugin: `Peas.PluginInfo` instance.
    :param current_view: `ArtistView` where the Artists are shown.
    '''
    # singleton instance
    instance = None

    # properties
    progress = GObject.property(type=float, default=0)
    
    def __init__(self, plugin, album_manager, shell):
        super(ArtistManager, self).__init__()

        self.db = plugin.shell.props.db
        self.shell = shell
        self.plugin = plugin

        self.cover_man = ArtistCoverManager(plugin, self)
        self.cover_man.album_manager = album_manager

        self.model = ArtistsModel(album_manager)
        self.loader = ArtistLoader(self, album_manager)
 
        # connect signals
        self._connect_signals()

    def _connect_signals(self):
        '''
        Connects the manager to all the needed signals for it to work.
        '''
        self.loader.connect('model-load-finished', self._load_finished_callback)
        
    def _load_finished_callback(self, *args):
        self.cover_man.load_covers()
        
class ArtistShowingPolicy(GObject.Object):
    '''
    Policy that mostly takes care of how and when things should be showed on
    the view that makes use of the `AlbumsModel`.
    '''

    def __init__(self, flow_view):
        super(ArtistShowingPolicy, self).__init__()

        self._flow_view = flow_view
        self.counter = 0
        self._has_initialised = False

    def initialise(self, album_manager):
        if self._has_initialised:
            return

        self._has_initialised = True
        self._album_manager = album_manager
        self._model = album_manager.model
        
class ArtistView(Gtk.TreeView, AbstractView):
    __gtype_name__ = "ArtistView"

    name = 'artistview'
    icon_automatic = GObject.property(type=bool, default=True)
    panedposition = PanedCollapsible.Paned.COLLAPSE
    
    __gsignals__ = {
        'update-toolbar': (GObject.SIGNAL_RUN_LAST, None, ())
        }
    

    def __init__(self, *args, **kwargs):
        super(ArtistView, self).__init__(*args, **kwargs)
        
        self.ext_menu_pos = 0
        self._external_plugins = None
        self.gs = GSetting()
        self.show_policy = ArtistShowingPolicy(self)
        self.view = self
        self._has_initialised = False        
            
    def initialise(self, source):
        if self._has_initialised:
            return
            
        self._has_initialised = True

        self.view_name = "artist_view"
        super(ArtistView, self).initialise(source)
        self.album_manager = source.album_manager
        self.shell = source.shell
        self.ext_menu_pos = 6
        
        self.set_enable_tree_lines(True)
        
        pixbuf = Gtk.CellRendererPixbuf()
        col = Gtk.TreeViewColumn('', pixbuf, pixbuf=1)
        
        self.append_column(col)
        
        col = Gtk.TreeViewColumn(_('Track Artist'), Gtk.CellRendererText(), text=0)
        col.set_sort_column_id(0)
        col.set_sort_indicator(True)
        self.append_column(col)
        col = Gtk.TreeViewColumn('', Gtk.CellRendererText(), text=4)
        self.append_column(col) # dummy column to expand horizontally
        
        self.artist_manager = self.album_manager.artist_man
        self.set_model(self.artist_manager.model.store)
        
        # setup iconview drag&drop support
        # first drag and drop on the coverart view to receive coverart
        self.enable_model_drag_dest([], Gdk.DragAction.COPY)
        self.drag_dest_add_image_targets()
        self.drag_dest_add_text_targets()
        self.connect('drag-drop', self.on_drag_drop)
        self.connect('drag-data-received',
            self.on_drag_data_received)
        self.props.has_tooltip = True
        self._connect_properties()
        self._connect_signals()
        
    def _connect_properties(self):
        setting = self.gs.get_setting(self.gs.Path.PLUGIN)
        setting.bind(self.gs.PluginKey.ICON_AUTOMATIC, self,
            'icon_automatic', Gio.SettingsBindFlags.GET)
        
    def _connect_signals(self):
        self.connect('row-activated', self._row_activated)
        self.connect('row-expanded', self._row_expanded)
        self.connect('button-press-event', self._row_click)
        self.get_selection().connect('changed', self._selection_changed)
        self.connect('query-tooltip', self._query_tooltip)
        
    def _query_tooltip( self, widget, x, y, key, tooltip ):
        
        try:
            winx, winy = self.convert_widget_to_bin_window_coords(x, y)
            treepath, treecolumn, cellx, celly = self.get_path_at_pos(winx, winy)
            active_object = self.artist_manager.model.get_from_path(treepath)
            
            if isinstance(active_object, Artist) and \
                treecolumn.get_title() == "" and \
                active_object.cover.original != self.artist_manager.cover_man.unknown_cover.original:
                # we display the tooltip if the row is an artist and the column
                # is actually the artist cover itself
                pixbuf = create_pixbuf_from_file_at_size(
                    active_object.cover.original, 256, 256)
                tooltip.set_icon( pixbuf )
                return True
            else:
                return False

        except:
            pass
            
    def _row_expanded(self, treeview, treeiter, treepath):
        '''
        event called when clicking the expand icon on the treeview
        '''
        self._row_activated(treeview, treepath, _)
        
    def _row_activated(self, treeview, treepath, treeviewcolumn):
        '''
        event called when double clicking on the tree-view or by keyboard ENTER
        '''
        active_object = self.artist_manager.model.get_from_path(treepath)
        if isinstance(active_object, Artist):
            self.artist_manager.model.emit('update-path', treepath)
        else:
            #we need to play this album
            self.source.play_selected_album(self.source.favourites)
            
    def _row_click(self, widget, event):
        '''
        event called when clicking on a row
        '''
        
        try:
            treepath, treecolumn, cellx, celly = self.get_path_at_pos(event.x, event.y)
        except:
            return
            
        active_object = self.artist_manager.model.get_from_path(treepath)
        
        if not isinstance(active_object, Album):
            if treecolumn != self.get_expander_column():
                if self.row_expanded(treepath):
                    self.collapse_row(treepath)
                else:
                    self.expand_row(treepath, False)
            return
            
        if event.button == 1:
            # on click
            # to expand the entry view
            ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
            shift = event.state & Gdk.ModifierType.SHIFT_MASK

            if self.icon_automatic:
                self.source.click_count += 1 if not ctrl and not shift else 0

            if self.source.click_count == 1:
                Gdk.threads_add_timeout(GLib.PRIORITY_DEFAULT_IDLE, 250,
                    self.source.show_hide_pane, active_object)
            
        elif event.button ==3:
            # on right click
            # display popup

            if not self._external_plugins:
                # initialise external plugin menu support
                self._external_plugins = \
                CreateExternalPluginMenu("ca_covers_view",
                    self.ext_menu_pos, self.popup)
                self._external_plugins.create_menu('popup_menu', True)
            
            self.popup.get_gtkmenu(self.source, 'popup_menu').popup(None,
                            None, 
                            None,
                            None,
                            3,
                            Gtk.get_current_event_time())
            
    def get_view_icon_name(self):
        return "artistview.png"
        
    def _selection_changed(self, *args):
        selected = self.get_selected_objects()

        if isinstance(selected[0], Artist):
        
            # clear the entry view
            self.source.entry_view.clear()

            cover_search_pane_visible = self.source.notebook.get_current_page() == \
                self.source.notebook.page_num(self.source.cover_search_pane)
                
            if not selected:
                # clean cover tab if selected
                if cover_search_pane_visible:
                    self.source.cover_search_pane.clear()

                return
            
            # update the cover search pane with the first selected artist
            if cover_search_pane_visible:
                self.source.cover_search_pane.do_search(selected[0],
                    self.artist_manager.cover_man.update_cover)
            
        else:
            self.source.update_with_selection()

    def get_selected_objects(self):
        '''
        finds what has been selected

        returns an array of `Album`
        '''
        selection = self.get_selection()
        model, treeiter = selection.get_selected()
        if treeiter:
            active_object = model.get_value(treeiter,ArtistsModel.columns['artist_album'])
            #if isinstance(active_object, Album):
            return [active_object]
        
        return []
        
    def switch_to_view(self, source, album):
        self.initialise(source)
        self.show_policy.initialise(source.album_manager)
        
        
        #if album:
        #    path = source.album_manager.model.get_path(album)
        #    self.select_and_scroll_to_path(path)
        
    def do_update_toolbar(self, *args):
        self.source.toolbar_manager.set_enabled(False, ToolbarObject.SORT_BY)
        self.source.toolbar_manager.set_enabled(False, ToolbarObject.SORT_ORDER)
        
    def on_drag_drop(self, widget, context, x, y, time):
        '''
        Callback called when a drag operation finishes over the view
        of the source. It decides if the dropped item can be processed as
        an image to use as a cover.
        '''

        # stop the propagation of the signal (deactivates superclass callback)
        if rb3compat.is_rb3(self.shell):
            widget.stop_emission_by_name('drag-drop')
        else:
            widget.stop_emission('drag-drop')

        # obtain the path of the icon over which the drag operation finished
        drop_info = self.get_dest_row_at_pos(x, y)
        path = None
        if drop_info:
            path, position = drop_info
            
        result = path is not None

        if result:
            target = self.drag_dest_find_target(context, None)
            widget.drag_get_data(context, target, time)

        return result

    def on_drag_data_received(self, widget, drag_context, x, y, data, info,
        time):
        '''
        Callback called when the drag source has prepared the data (pixbuf)
        for us to use.
        '''

        # stop the propagation of the signal (deactivates superclass callback)
        if rb3compat.is_rb3(self.shell):
            widget.stop_emission_by_name('drag-data-received')
        else:
            widget.stop_emission('drag-data-received')

        # get the artist and the info and ask the loader to update the cover
        path, position = self.get_dest_row_at_pos(x, y)
        artist_album = widget.get_model()[path][2]

        pixbuf = data.get_pixbuf()     

        if isinstance(artist_album, Album):
            manager = self.album_manager
        else:
            manager = self.artist_manager
            
        if pixbuf:
            manager.cover_man.update_cover(artist_album, pixbuf)
        else:
            uri = data.get_text()
            
            manager.cover_man.update_cover(artist_album, uri=uri)

        # call the context drag_finished to inform the source about it
        drag_context.finish(True, False, time)
        
    def get_default_manager(self):
        '''
        the default manager for this view is the artist_manager
        '''
        return self.artist_manager
