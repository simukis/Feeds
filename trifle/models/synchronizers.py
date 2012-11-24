import json
import os
import itertools
import re

from trifle.utils import logger
from trifle.models import settings
from trifle.models import utils
from trifle.models import auth
from trifle.models import base


class Id(base.SyncObject):
    states = {'reading-list': [('s', 'user/-/state/com.google/reading-list')],
              'unread': [('s', 'user/-/state/com.google/reading-list'),
                         ('xt', 'user/-/state/com.google/read')],
              'starred': [('s', 'user/-/state/com.google/starred')]}

    def __init__(self, *args, **kwargs):
        super(Id, self).__init__(*args, **kwargs)
        self.sync_status = {}
        self.connect('notify::sync-status', self.on_status_change)

    def sync(self):
        if self.sync_status.get('synchronizing', False):
            logger.error('IDs are already being synchronized')
            return False
        self.sync_status['synchronizing'] = True

        item_limit = settings.settings['cache-items']
        for name, state in self.states.items():
            getargs = state + [('n', item_limit)]
            url = utils.api_method('stream/items/ids', getargs)
            msg = auth.auth.message('GET', url)
            utils.session.queue_message(msg, self.on_response, name)
        # Initially mark everything as deletable and unflag all items.
        # Laten in process items that are still important will be unmarked
        # and reflagged again.
        query = 'UPDATE items SET to_delete=1, unread=0, starred=0, to_sync=0'
        utils.sqlite.execute(query)

    def on_response(self, session, msg, data):
        status = msg.status_code
        if not 200 <= status < 400:
            logger.error('IDs synchronization failed: {0}'.format(status))
            return False

        res = json.loads(msg.response_body.data)['itemRefs']
        id_list = [(int(i['id']),) for i in res]
        self.ensure_ids(id_list)
        self.set_sync_flag({'update_time': int(i['timestampUsec']),
                            'id': int(i['id'])} for i in res)
        if data in ['unread', 'starred']:
            self.set_flag(data, id_list)

        self.sync_status[data] = True
        self.notify('sync-status')

    def ensure_ids(self, id_list):
        # We'll insert any ids we don't yet have in our database
        query = 'INSERT OR IGNORE INTO items(id) VALUES(?)'
        utils.sqlite.executemany(query, id_list)
        # And set to_delete flag to zero for all ids we've got.
        # This way all the items with to_delete flag set are too old.
        query = 'UPDATE items SET to_delete=0 WHERE id=?'
        utils.sqlite.executemany(query, id_list)

    def set_sync_flag(self, items):
        query = '''UPDATE items SET to_sync=1, update_time=:update_time WHERE
                   id=:id AND update_time<:update_time'''
        utils.sqlite.executemany(query, items)

    def set_flag(self, flag, id_list):
        query = 'UPDATE items SET {0}=1 WHERE id=?'.format(flag)
        utils.sqlite.executemany(query, id_list)

    @staticmethod
    def on_status_change(self, gprop):
        if all(self.sync_status.get(key, False) for key in self.states.keys()):
            logger.debug('IDs synchronizaton completed')
            utils.sqlite.commit()
            self.emit('sync-done')


class Flags(base.SyncObject):
    flags = {'read': 'user/-/state/com.google/read',
             'kept-unread': 'user/-/state/com.google/kept-unread',
             'starred': 'user/-/state/com.google/starred'}

    def __init__(self, *args, **kwargs):
        super(Flags, self).__init__(*args, **kwargs)
        self.sync_status = 0

    def sync(self):
        if self.sync_status > 0:
            logger.error('Flags are already being synchronized')
            return False
        self.sync_status = 0
        uri = utils.api_method('edit-tag')
        req_type = 'application/x-www-form-urlencoded'
        query = 'SELECT item_id, id FROM flags WHERE flag=? AND remove=?'

        for flag, st in itertools.product(self.flags.values(), [True, False]):
            result = utils.sqlite.execute(query, (flag, st,)).fetchall()
            if len(result) == 0:
                continue

            post = (('r' if st else 'a', flag,), ('T', auth.auth.edit_token),)
            chunks = utils.split_chunks(result, 250, None)
            for chunk in chunks:
                iids, ids = zip(*filter(lambda x: x is not None, chunk))
                iids = tuple(zip(itertools.repeat('i'), iids))
                payload = utils.urlencode(iids + post)
                msg = auth.auth.message('POST', uri)
                msg.set_request(req_type, utils.Soup.MemoryUse.COPY, payload,
                                len(payload))
                utils.session.queue_message(msg, self.on_response, ids)
                self.sync_status += 1

        if self.sync_status == 0:
            # In case we didn't have any flags to synchronize
            logger.debug('There were no flags to synchronize')
            self.emit('sync-done')

    def on_response(self, session, message, data):
        self.sync_status -= 1
        if self.sync_status == 0:
            logger.debug('Flags synchronizaton completed')
            self.emit('sync-done')

        status = message.status_code
        if not 200 <= status < 400:
            logger.error('Flags synchronizaton failed {0}'.format(status))
            return False

        data = ((i,) for i in data)
        utils.sqlite.executemany('DELETE FROM flags WHERE id=?', data)
        if self.sync_status == 0:
            utils.sqlite.commit()


class Items(base.SyncObject):

    def __init__(self, *args, **kwargs):
        super(Items, self).__init__(*args, **kwargs)
        self.sync_status = 0

    def sync(self):
        if self.sync_status > 0:
            logger.warning('Items are already being synchronized')
            return
        self.sync_status = 0
        logger.debug('Synchronizing items')
        uri = utils.api_method('stream/items/contents')
        req_type = 'application/x-www-form-urlencoded'
        self.dump_garbage()

        # Somewhy when streaming items and asking more than 512 returns 400.
        # Asking anything in between 250 and 512 returns exactly 250 items.
        ids = utils.sqlite.execute('SELECT id FROM items WHERE to_sync=1')
        ids = ids.fetchall()
        if len(ids) == 0:
            logger.debug('Items doesn\'t need synchronization')
            self.post_sync()
            return False

        chunks = utils.split_chunks((('i', i) for i, in ids), 250, ('', ''))
        for chunk in chunks:
            self.sync_status += 1
            data = utils.urlencode(chunk)
            message = auth.auth.message('POST', uri)
            message.set_request(req_type, utils.Soup.MemoryUse.COPY, data,
                                len(data))
            utils.session.queue_message(message, self.process_response, None)
        self.connect('notify::sync-status', self.post_sync)

    def process_response(self, session, message, data=None):
        status = message.status_code
        if not 200 <= status < 400:
            logger.error('Items synchronization failed: {0}' .format(status))
        else:
            data = json.loads(message.response_body.data)
            for item in data['items']:
                sid = utils.short_id(item['id'])
                metadata, content = self.process_item(item)
                metadata.update({'id': sid})
                self.save_content(sid, content)
                query = '''UPDATE items SET title=:title, author=:author,
                           summary=:summary, href=:href, time=:time,
                           subscription=:subscription WHERE id=:id'''
                utils.sqlite.execute(query, metadata)
        self.sync_status -= 1

    def post_sync(self, *args):
        if self.sync_status != 0:
            return
        utils.sqlite.commit()
        self.emit('sync-done')

    def dump_garbage(self):
        """ Remove all items (and contents) marked with to_delete flag """
        query = 'SELECT id FROM items WHERE to_delete=1'
        ids = utils.sqlite.execute(query).fetchall()
        utils.sqlite.execute('DELETE FROM items WHERE to_delete=1')
        for i, in ids:
            self.save_content(i, None)

    def save_content(self, item_id, content):
        fpath = os.path.join(utils.content_dir, str(item_id))
        if content is None and os.path.isfile(fpath):
            os.remove(fpath)
        else:
            with open(fpath, 'w') as f:
                f.write(content)


    def process_item(self, item):
        """
        Should return a (dictionary, content,) pair.
        Dictionary should contain subscription, time, href, author, title and
        summary fields.
        If any of values doesn't exist, they'll be replaced with meaningful
        defaults. For example "Unknown" for author or "Untitled item" for
        title
        """
        # After a lot of fiddling around I realized one thing. We are IN NO
        # WAY guaranteed that any of these fields exists at all.
        # This idiocy should make this method bigger than a manpage for
        # understanding teenage girls' thought processes.
        def strip(text):
            if not text:
                return text
            text = strip.html_re.sub('', text).strip()
            return strip.space_re.sub('', text)
        strip.html_re = re.compile('<.+?>')
        strip.space_re = re.compile('[\t\n\r]+')

        result = {}
        result['subscription'] = item['origin']['streamId']
        result['author'] = item.get('author', None)
        # How could they even think of putting html into feed title?!
        result['title'] = strip(item.get('title', None))

        result['time'] = int(item['timestampUsec'])
        if result['time'] >= int(item.get('updated', -1)) * 1E6:
            result['time'] = item['updated'] * 1E6

        try:
            result['href'] = item['alternate'][0]['href']
        except KeyError:
            result['href'] = item['origin']['htmlUrl']

        content = item['summary']['content'] if 'summary' in item else \
                  item['content']['content'] if 'content' in item else ''
        result['summary'] = strip(content)[:512]

        for k in ['author', 'title', 'summary']:
            result[k] = utils.escape(result[k]) if result[k] else result[k]
        if len(result['summary']) > 140:
            result['summary'] = result['summary'][:139] + '…'

        return result, content