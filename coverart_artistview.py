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

from coverart_browser_prefs import GSetting
from coverart_album import Cover
from coverart_widgets import AbstractView
from coverart_utils import SortedCollection
import rb

from collections import namedtuple

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
        if self._cover:
            self._cover.disconnect(self._cover_resized_id)

        self._cover = new_cover
        self._cover_resized_id = self._cover.connect('resized',
            lambda *args: self.emit('cover-updated'))

        self.emit('cover-updated')

class ArtistsModel(GObject.Object):
    '''
    Model that contains artists, keeps them sorted, filtered and provides an
    external `Gtk.TreeModel` interface to use as part of a Gtk interface.

    The `Gtk.TreeModel` haves the following structure:
    column 0 -> string containing the artist name
    column 1 -> pixbuf of the artist's cover.
    column 2 -> instance of the artist itself.
    column 3 -> boolean that indicates if the row should be shown
    '''
    # signals
    __gsignals__ = {
        'generate-tooltip': (GObject.SIGNAL_RUN_LAST, str, (object,)),
        'generate-markup': (GObject.SIGNAL_RUN_LAST, str, (object,)),
        'album-updated': ((GObject.SIGNAL_RUN_LAST, None, (object, object))),
        'visual-updated': ((GObject.SIGNAL_RUN_LAST, None, (object, object))),
        'filter-changed': ((GObject.SIGNAL_RUN_FIRST, None, ()))
        }

    # list of columns names and positions on the TreeModel
    columns = {'tooltip': 0, 'pixbuf': 1, 'artist': 2, 'show': 3}

    def __init__(self):
        super(ArtistsModel, self).__init__()

        self._iters = {}
        self._artists = SortedCollection(
            key=lambda artist: getattr(artist, 'name'))

        self._tree_store = Gtk.TreeStore(str, GdkPixbuf.Pixbuf, object, 
            bool)

        # filters
        self._filters = {}

        # sorting idle call
        self._sort_process = None

        # create the filtered store that's used with the view
        self._filtered_store = self._tree_store.filter_new()
        self._filtered_store.set_visible_column(ArtistsModel.columns['show'])

    @property
    def store(self):
        return self._filtered_store

    def add(self, artist):
        '''
        Add an artist to the model.

        :param artist: `Artist` to be added to the model.
        '''
        # generate necessary values
        values = self._generate_values(artist)
        # insert the values
        x = values
        print x
        y = self._artists.insert(artist)
        print y
        
        tree_iter = self._tree_store.insert(None,self._artists.insert(artist), values)
        if not artist.name in self._iters:
            self._iters[artist.name] = {}
        self._iters[artist.name] = {'artist': artist,
            'iter': tree_iter}
        return tree_iter

    def _generate_values(self, artist):
        tooltip = artist.name
        pixbuf = artist.cover.pixbuf
        hidden = self._artist_filter(artist)

        return tooltip, pixbuf, artist, hidden

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
        return self._iters[artist]['artist']
        
    def get_from_path(self, path):
        '''
        Returns an artist referenced by a `Gtk.TreeModel` path.

        :param path: `Gtk.TreePath` referencing the artist.
        '''
        return self._filtered_store[path][self.columns['album']]

    def get_path(self, artist):
        return self._filtered_store.convert_child_path_to_path(
            self._tree_store.get_path(
                self._iters[artist.name]['iter']))

    def show(self, artist, show):
        '''
        Unfilters an artist, making it visible to the publicly available model's
        `Gtk.TreeModel`

        :param artist: `Artist` to show or hide.
        :param show: `bool` indcating whether to show(True) or hide(False) the
            album.
        '''
        artist_iter = self._iters[artist.name]['iter']

        if self._tree_store.iter_is_valid(artist_iter):
            self._tree_store.set_value(artist_iter, self.columns['show'], show)

    def sort(self, key=None, reverse=False):
        '''
        Changes the sorting strategy for the model.

        :param key: `str`attribute of the `Artist` class by which the sort
            should be performed.
        :param reverse: `bool` indicating whether the sort order should be
            reversed from the current one.
        '''
        if key:
            props = sort_keys[key]

            def key_function(artist):
                keys = [getattr(artist, prop) for prop in props]
                return keys

            self._artists.key = key_function

        if reverse:
            self._artists = reversed(self._artists)

        self._tree_store.clear()

        # add the nay filter
        self.replace_filter('nay', refilter=False)

        if self._sort_process:
            # stop the previous sort process if there's one
            self._sort_process.stop()

        # load the albums back to the model
        self._sort_process = self._sort(iter(self._artists))
        
    def _artist_filter(self, artist):
            for f in list(self._filters.values()):
                if not f(artist):
                    return False

            return True

        
class ArtistLoader(GObject.Object):
    '''
    Loads Artists - updating the model accordingly.

    :param artistmanager: `ArtistManager` responsible for this loader.
    '''
    # signals
    __gsignals__ = {
        'albums-load-finished': (GObject.SIGNAL_RUN_LAST, None, (object,)),
        'model-load-finished': (GObject.SIGNAL_RUN_LAST, None, ())
        }

    def __init__(self, artistmanager):
        super(ArtistLoader, self).__init__()

        self.shell = artistmanager.shell
        self._connect_signals()
        
        self.cover_size = 128
        
        self.unknown_cover = Cover(self.cover_size, 
            rb.find_plugin_file(artistmanager.plugin, 'img/rhythmbox-missing-artwork.svg'))
        self.model = artistmanager.model
    
        artist_pview = None
        for view in self.shell.props.library_source.get_property_views():
            if view.props.title == _("Artist"):
                artist_pview = view
                break

        assert artist_pview, "cannot find artist property view"

        for row in artist_pview.get_model():
            self.model.add(Artist(row[0], self.unknown_cover))
        
    def _connect_signals(self):
        # connect signals for updating the albums
        #self.entry_changed_id = self._album_manager.db.connect('entry-changed',
        #    self._entry_changed_callback)
        pass
        
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

    def __init__(self, plugin, current_view, shell):
        super(ArtistManager, self).__init__()

        self.current_view = current_view
        self.db = plugin.shell.props.db
        self.shell = shell
        self.plugin = plugin

        self.model = ArtistsModel()
        self._loader = ArtistLoader(self)
        # connect signals
        self._connect_signals()

    def _connect_signals(self):
        '''
        Connects the manager to all the needed signals for it to work.
        '''
        # connect signal to the loader so it shows the albums when it finishes
        pass

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
        self.source = source
        self.plugin = source.plugin
        self.shell = source.shell
        self.ext_menu_pos = 6
        
        self._connect_properties()
        self._connect_signals()
        
        self.set_enable_tree_lines(True)
        self.set_grid_lines(Gtk.TreeViewGridLines.BOTH)
        
        col = Gtk.TreeViewColumn(_('Track Artist'), Gtk.CellRendererText(), text=0)
        col.set_expand(True)
        
        self.append_column(col)
        
        self.artistmanager = ArtistManager(self.plugin, self, self.shell)
        self.set_model(self.artistmanager.model.store)
        
    def _connect_properties(self):
        setting = self.gs.get_setting(self.gs.Path.PLUGIN)
        
    def _connect_signals(self):
        pass

    def get_view_icon_name(self):
        return "artistview.png"
