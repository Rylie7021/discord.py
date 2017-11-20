# -*- coding: utf-8 -*-

"""
The MIT License (MIT)

Copyright (c) 2015-2017 Rapptz

Permission is hereby granted, free of charge, to any person obtaining a
copy of this software and associated documentation files (the "Software"),
to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense,
and/or sell copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
DEALINGS IN THE SOFTWARE.
"""

import aiohttp
import asyncio
import json
import sys
import logging
import weakref
import datetime
from urllib.parse import quote as _uriquote

log = logging.getLogger(__name__)

from .errors import HTTPException, Forbidden, NotFound, LoginFailure, GatewayNotFound
from . import __version__, utils

@asyncio.coroutine
def json_or_text(response):
    text = yield from response.text(encoding='utf-8')
    if response.headers['content-type'] == 'application/json':
        return json.loads(text)
    return text

class Route:
    BASE = 'https://discordapp.com/api/v7'

    def __init__(self, method, path, **parameters):
        self.path = path
        self.method = method
        url = (self.BASE + self.path)
        try:
            parameters['channel_id'] = parameters.get('channel').id
        except:
            pass
        if parameters:
            self.url = url.format(**{k: _uriquote(v) if isinstance(v, str) else v for k, v in parameters.items()})
        else:
            self.url = url

        # major parameters:
        self.channel = parameters.get('channel')
        self.channel_id = parameters.get('channel_id')
        self.guild_id = parameters.get('guild_id')

    @property
    def bucket(self):
        # the bucket is just method + path w/ major parameters
        return '{0.method}:{0.channel_id}:{0.guild_id}:{0.path}'.format(self)

class MaybeUnlock:
    def __init__(self, lock):
        self.lock = lock
        self._unlock = True

    def __enter__(self):
        return self

    def defer(self):
        self._unlock = False

    def __exit__(self, type, value, traceback):
        if self._unlock:
            self.lock.release()

class HTTPClient:
    """Represents an HTTP client sending HTTP requests to the Discord API."""

    SUCCESS_LOG = '{method} {url} has received {text}'
    REQUEST_LOG = '{method} {url} with {json} has returned {status}'

    def __init__(self, connector=None, *, proxy=None, proxy_auth=None, loop=None):
        self.loop = asyncio.get_event_loop() if loop is None else loop
        self.connector = connector
        self._session = aiohttp.ClientSession(connector=connector, loop=self.loop)
        self._locks = weakref.WeakValueDictionary()
        self._global_over = asyncio.Event(loop=self.loop)
        self._global_over.set()
        self.token = None
        self.bot_token = False
        self.proxy = proxy
        self.proxy_auth = proxy_auth

    @asyncio.coroutine
    def request(self, route, *, header_bypass_delay=None, **kwargs):
        bucket = route.bucket
        method = route.method
        url = route.url
        channel = route.channel

        lock = self._locks.get(bucket)
        if lock is None:
            lock = asyncio.Lock(loop=self.loop)
            if bucket is not None:
                self._locks[bucket] = lock

        # header creation
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) discord/0.0.179 Chrome/58.0.3029.110 Discord Canary/1.7.9 Safari/537.36',
                'referer': f'https://canary.discordapp.com/channels/{channel.guild.id}/{channel.id}',
                'x-super-properties': 'eyJvcyI6IldpbmRvd3MiLCJicm93c2VyIjoiRGlzY29yZCBDbGllbnQiLCJyZWxlYXNlX2NoYW5uZWwiOiJjYW5hcnkiLCJjbGllbnRfdmVyc2lvbiI6IjAuMC4xNzkiLCJvc192ZXJzaW9uIjoiMTAuMC4xNTA2MyJ9'
                }
        except:
            try:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) discord/0.0.179 Chrome/58.0.3029.110 Discord Canary/1.7.9 Safari/537.36',
                    'referer': f'https://canary.discordapp.com/channels/{guild.id}',
                    'x-super-properties': 'eyJvcyI6IldpbmRvd3MiLCJicm93c2VyIjoiRGlzY29yZCBDbGllbnQiLCJyZWxlYXNlX2NoYW5uZWwiOiJjYW5hcnkiLCJjbGllbnRfdmVyc2lvbiI6IjAuMC4xNzkiLCJvc192ZXJzaW9uIjoiMTAuMC4xNTA2MyJ9'
                }
            except:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) discord/0.0.179 Chrome/58.0.3029.110 Discord Canary/1.7.9 Safari/537.36',
                    'referer': f'https://canary.discordapp.com/channels/@me',
                    'x-super-properties': 'eyJvcyI6IldpbmRvd3MiLCJicm93c2VyIjoiRGlzY29yZCBDbGllbnQiLCJyZWxlYXNlX2NoYW5uZWwiOiJjYW5hcnkiLCJjbGllbnRfdmVyc2lvbiI6IjAuMC4xNzkiLCJvc192ZXJzaW9uIjoiMTAuMC4xNTA2MyJ9'
                }

        if self.token is not None:
            headers['Authorization'] = 'Bot ' + self.token if self.bot_token else self.token
        # some checking if it's a JSON request
        if 'json' in kwargs:
            headers['Content-Type'] = 'application/json'
            kwargs['data'] = utils.to_json(kwargs.pop('json'))

        try:
            reason = kwargs.pop('reason')
        except KeyError:
            pass
        else:
            if reason:
                headers['X-Audit-Log-Reason'] = _uriquote(reason, safe='/ ')

        kwargs['headers'] = headers

        # Proxy support
        if self.proxy is not None:
            kwargs['proxy'] = self.proxy
        if self.proxy_auth is not None:
            kwargs['proxy_auth'] = self.proxy_auth

        if not self._global_over.is_set():
            # wait until the global lock is complete
            yield from self._global_over.wait()

        yield from lock
        with MaybeUnlock(lock) as maybe_lock:
            for tries in range(5):
                r = yield from self._session.request(method, url, **kwargs)
                log.debug('%s %s with %s has returned %s', method, url, kwargs.get('data'), r.status)
                try:
                    # even errors have text involved in them so this is safe to call
                    data = yield from json_or_text(r)

                    # check if we have rate limit header information
                    remaining = r.headers.get('X-Ratelimit-Remaining')
                    if remaining == '0' and r.status != 429:
                        # we've depleted our current bucket
                        if header_bypass_delay is None:
                            delta = utils._parse_ratelimit_header(r)
                        else:
                            delta = header_bypass_delay

                        log.debug('A rate limit bucket has been exhausted (bucket: %s, retry: %s).', bucket, delta)
                        maybe_lock.defer()
                        self.loop.call_later(delta, lock.release)

                    # the request was successful so just return the text/json
                    if 300 > r.status >= 200:
                        log.debug('%s %s has received %s', method, url, data)
                        return data

                    # we are being rate limited
                    if r.status == 429:
                        fmt = 'We are being rate limited. Retrying in %.2f seconds. Handled under the bucket "%s"'

                        # sleep a bit
                        retry_after = data['retry_after'] / 1000.0
                        log.info(fmt, retry_after, bucket)

                        # check if it's a global rate limit
                        is_global = data.get('global', False)
                        if is_global:
                            log.info('Global rate limit has been hit. Retrying in %.2f seconds.', retry_after)
                            self._global_over.clear()

                        yield from asyncio.sleep(retry_after, loop=self.loop)
                        log.debug('Done sleeping for the rate limit. Retrying...')

                        # release the global lock now that the
                        # global rate limit has passed
                        if is_global:
                            self._global_over.set()
                            log.debug('Global rate limit is now over.')

                        continue

                    # we've received a 500 or 502, unconditional retry
                    if r.status in {500, 502}:
                        yield from asyncio.sleep(1 + tries * 2, loop=self.loop)
                        continue

                    # the usual error cases
                    if r.status == 403:
                        raise Forbidden(r, data)
                    elif r.status == 404:
                        raise NotFound(r, data)
                    else:
                        raise HTTPException(r, data)
                finally:
                    # clean-up just in case
                    yield from r.release()
            # We've run out of retries, raise.
            raise HTTPException(r, data)

    def get_attachment(self, url):
        resp = yield from self._session.get(url)
        try:
            if resp.status == 200:
                return (yield from resp.read())
            elif resp.status == 404:
                raise NotFound(resp, 'attachment not found')
            elif resp.status == 403:
                raise Forbidden(resp, 'cannot retrieve attachment')
            else:
                raise HTTPException(resp, 'failed to get attachment')
        finally:
            yield from resp.release()

    # state management

    @asyncio.coroutine
    def close(self):
        yield from self._session.close()

    def _token(self, token, *, bot=True):
        self.token = token
        self.bot_token = bot
        self._ack_token = None

    # login management

    @asyncio.coroutine
    def static_login(self, token, *, bot):
        old_token, old_bot = self.token, self.bot_token
        self._token(token, bot=bot)

        try:
            data = yield from self.request(Route('GET', '/users/@me'))
        except HTTPException as e:
            self._token(old_token, bot=old_bot)
            if e.response.status == 401:
                raise LoginFailure('Improper token has been passed.') from e
            raise e

        return data

    def logout(self):
        return self.request(Route('POST', '/auth/logout'))

    # Group functionality

    def start_group(self, user_id, recipients):
        payload = {
            'recipients': recipients
        }

        return self.request(Route('POST', '/users/{user_id}/channels', user_id=user_id), json=payload)

    def leave_group(self, channel):
        return self.request(Route('DELETE', '/channels/{channel.id}', channel=channel))

    def add_group_recipient(self, channel, user_id):
        r = Route('PUT', '/channels/{channel.id}/recipients/{user_id}', channel=channel, user_id=user_id)
        return self.request(r)

    def remove_group_recipient(self, channel, user_id):
        r = Route('DELETE', '/channels/{channel.id}/recipients/{user_id}', channel=channel, user_id=user_id)
        return self.request(r)

    def edit_group(self, channel, **options):
        valid_keys = ('name', 'icon')
        payload = {
            k: v for k, v in options.items() if k in valid_keys
        }

        return self.request(Route('PATCH', '/channels/{channel.id}', channel=channel), json=payload)

    def convert_group(self, channel):
        return self.request(Route('POST', '/channels/{channel.id}/convert', channel=channel))

    # Message management

    def start_private_message(self, user_id):
        payload = {
            'recipient_id': user_id
        }

        return self.request(Route('POST', '/users/@me/channels'), json=payload)

    def send_message(self, channel, content, *, tts=False, embed=None, nonce=None):
        r = Route('POST', '/channels/{channel.id}/messages', channel=channel)
        payload = {}

        if content:
            payload['content'] = content

        if tts:
            payload['tts'] = True

        if embed:
            payload['embed'] = embed

        if nonce:
            payload['nonce'] = nonce

        return self.request(r, json=payload)

    def send_typing(self, channel):
        return self.request(Route('POST', '/channels/{channel.id}/typing', channel=channel))

    def send_files(self, channel, *, files, content=None, tts=False, embed=None, nonce=None):
        r = Route('POST', '/channels/{channel.id}/messages', channel=channel)
        form = aiohttp.FormData()

        payload = {'tts': tts}
        if content:
            payload['content'] = content
        if embed:
            payload['embed'] = embed
        if nonce:
            payload['nonce'] = nonce

        form.add_field('payload_json', utils.to_json(payload))
        if len(files) == 1:
            fp = files[0]
            form.add_field('file', fp[0], filename=fp[1], content_type='application/octet-stream')
        else:
            for index, (buffer, filename) in enumerate(files):
                form.add_field('file%s' % index, buffer, filename=filename, content_type='application/octet-stream')

        return self.request(r, data=form)

    @asyncio.coroutine
    def ack_message(self, channel, message_id):
        r = Route('POST', '/channels/{channel.id}/messages/{message_id}/ack', channel=channel,
                                                                          message_id=message_id)
        data = yield from self.request(r, json={'token': self._ack_token})
        self._ack_token = data['token']

    def ack_guild(self, guild_id):
        return self.request(Route('POST', '/guilds/{guild_id}/ack', guild_id=guild_id))

    def delete_message(self, channel, message_id, *, reason=None):
        r = Route('DELETE', '/channels/{channel.id}/messages/{message_id}', channel=channel,
                                                                            message_id=message_id)
        return self.request(r, reason=reason)

    def delete_messages(self, channel, message_ids, *, reason=None):
        r = Route('POST', '/channels/{channel.id}/messages/bulk_delete', channel=channel)
        payload = {
            'messages': message_ids
        }

        return self.request(r, json=payload, reason=reason)

    def edit_message(self, message_id, channel, **fields):
        r = Route('PATCH', '/channels/{channel.id}/messages/{message_id}', channel=channel,
                                                                           message_id=message_id)
        return self.request(r, json=fields)

    def add_reaction(self, message_id, channel, emoji):
        r = Route('PUT', '/channels/{channel.id}/messages/{message_id}/reactions/{emoji}/@me',
                  channel=channel, message_id=message_id, emoji=emoji)
        return self.request(r, header_bypass_delay=0.25)

    def remove_reaction(self, message_id, channel, emoji, member_id):
        r = Route('DELETE', '/channels/{channel.id}/messages/{message_id}/reactions/{emoji}/{member_id}',
                  channel=channel, message_id=message_id, member_id=member_id, emoji=emoji)
        return self.request(r, header_bypass_delay=0.25)

    def get_reaction_users(self, message_id, channel, emoji, limit, after=None):
        r = Route('GET', '/channels/{channel.id}/messages/{message_id}/reactions/{emoji}',
                         channel=channel, message_id=message_id, emoji=emoji)

        params = {'limit': limit}
        if after:
            params['after'] = after
        return self.request(r, params=params)

    def clear_reactions(self, message_id, channel):
        r = Route('DELETE', '/channels/{channel.id}/messages/{message_id}/reactions',
                  channel=channel, message_id=message_id)

        return self.request(r)

    def get_message(self, channel, message_id):
        r = Route('GET', '/channels/{channel.id}/messages/{message_id}', channel=channel, message_id=message_id)
        return self.request(r)

    def logs_from(self, channel, limit, before=None, after=None, around=None):
        params = {
            'limit': limit
        }

        if before:
            params['before'] = before
        if after:
            params['after'] = after
        if around:
            params['around'] = around

        return self.request(Route('GET', '/channels/{channel.id}/messages', channel=channel), params=params)

    def pin_message(self, channel, message_id):
        return self.request(Route('PUT', '/channels/{channel.id}/pins/{message_id}',
                            channel=channel, message_id=message_id))

    def unpin_message(self, channel, message_id):
        return self.request(Route('DELETE', '/channels/{channel.id}/pins/{message_id}',
                            channel=channel, message_id=message_id))

    def pins_from(self, channel):
        return self.request(Route('GET', '/channels/{channel.id}/pins', channel=channel))

    # Member management

    def kick(self, user_id, guild_id, reason=None):
        r = Route('DELETE', '/guilds/{guild_id}/members/{user_id}', guild_id=guild_id, user_id=user_id)
        if reason:
            # thanks aiohttp
            r.url = '{0.url}?reason={1}'.format(r, _uriquote(reason))

        return self.request(r)

    def ban(self, user_id, guild_id, delete_message_days=1, reason=None):
        r = Route('PUT', '/guilds/{guild_id}/bans/{user_id}', guild_id=guild_id, user_id=user_id)
        params = {
            'delete-message-days': delete_message_days,
        }

        if reason:
            # thanks aiohttp
            r.url = '{0.url}?reason={1}'.format(r, _uriquote(reason))

        return self.request(r, params=params)

    def unban(self, user_id, guild_id, *, reason=None):
        r = Route('DELETE', '/guilds/{guild_id}/bans/{user_id}', guild_id=guild_id, user_id=user_id)
        return self.request(r, reason=reason)

    def guild_voice_state(self, user_id, guild_id, *, mute=None, deafen=None, reason=None):
        r = Route('PATCH', '/guilds/{guild_id}/members/{user_id}', guild_id=guild_id, user_id=user_id)
        payload = {}
        if mute is not None:
            payload['mute'] = mute

        if deafen is not None:
            payload['deaf'] = deafen

        return self.request(r, json=payload, reason=reason)

    def edit_profile(self, password, username, avatar, **fields):
        payload = {
            'password': password,
            'username': username,
            'avatar': avatar
        }

        if 'email' in fields:
            payload['email'] = fields['email']

        if 'new_password' in fields:
            payload['new_password'] = fields['new_password']

        return self.request(Route('PATCH', '/users/@me'), json=payload)

    def change_my_nickname(self, guild_id, nickname, *, reason=None):
        r = Route('PATCH', '/guilds/{guild_id}/members/@me/nick', guild_id=guild_id)
        payload = {
            'nick': nickname
        }
        return self.request(r, json=payload, reason=reason)

    def change_nickname(self, guild_id, user_id, nickname, *, reason=None):
        r = Route('PATCH', '/guilds/{guild_id}/members/{user_id}', guild_id=guild_id, user_id=user_id)
        payload = {
            'nick': nickname
        }
        return self.request(r, json=payload, reason=reason)

    def edit_member(self, guild_id, user_id, *, reason=None, **fields):
        r = Route('PATCH', '/guilds/{guild_id}/members/{user_id}', guild_id=guild_id, user_id=user_id)
        return self.request(r, json=fields, reason=reason)

    # Channel management

    def edit_channel(self, channel, *, reason=None, **options):
        r = Route('PATCH', '/channels/{channel.id}', channel=channel)
        valid_keys = ('name', 'parent_id', 'topic', 'bitrate', 'nsfw', 'user_limit', 'position', 'permission_overwrites')
        payload = {
            k: v for k, v in options.items() if k in valid_keys
        }

        return self.request(r, reason=reason, json=payload)

    def bulk_channel_update(self, guild_id, data, *, reason=None):
        r = Route('PATCH', '/guilds/{guild_id}/channels', guild_id=guild_id)
        return self.request(r, json=data, reason=reason)

    def create_channel(self, guild_id, name, channel_type, parent_id=None, permission_overwrites=None, *, reason=None):
        payload = {
            'name': name,
            'type': channel_type
        }

        if permission_overwrites is not None:
            payload['permission_overwrites'] = permission_overwrites

        if parent_id is not None:
            payload['parent_id'] = parent_id

        return self.request(Route('POST', '/guilds/{guild_id}/channels', guild_id=guild_id), json=payload, reason=reason)

    def delete_channel(self, channel, *, reason=None):
        return self.request(Route('DELETE', '/channels/{channel.id}', channel=channel), reason=reason)

    # Webhook management

    def create_webhook(self, channel, *, name=None, avatar=None):
        payload = {}
        if name is not None:
            payload['name'] = name
        if avatar is not None:
            payload['avatar'] = avatar

        return self.request(Route('POST', '/channels/{channel.id}/webhooks', channel=channel), json=payload)

    def channel_webhooks(self, channel):
        return self.request(Route('GET', '/channels/{channel.id}/webhooks', channel=channel))

    def guild_webhooks(self, guild_id):
        return self.request(Route('GET', '/guilds/{guild_id}/webhooks', guild_id=guild_id))

    def get_webhook(self, webhook_id):
        return self.request(Route('GET', '/webhooks/{webhook_id}', webhook_id=webhook_id))

    # Guild management

    def leave_guild(self, guild_id):
        return self.request(Route('DELETE', '/users/@me/guilds/{guild_id}', guild_id=guild_id))

    def delete_guild(self, guild_id):
        return self.request(Route('DELETE', '/guilds/{guild_id}', guild_id=guild_id))

    def create_guild(self, name, region, icon):
        payload = {
            'name': name,
            'icon': icon,
            'region': region
        }

        return self.request(Route('POST', '/guilds'), json=payload)

    def edit_guild(self, guild_id, *, reason=None, **fields):
        valid_keys = ('name', 'region', 'icon', 'afk_timeout', 'owner_id',
                      'afk_channel_id', 'splash', 'verification_level',
                      'system_channel_id')

        payload = {
            k: v for k, v in fields.items() if k in valid_keys
        }

        return self.request(Route('PATCH', '/guilds/{guild_id}', guild_id=guild_id), json=payload, reason=reason)

    def get_bans(self, guild_id):
        return self.request(Route('GET', '/guilds/{guild_id}/bans', guild_id=guild_id))

    def get_vanity_code(self, guild_id):
        return self.request(Route('GET', '/guilds/{guild_id}/vanity-url', guild_id=guild_id))

    def change_vanity_code(self, guild_id, code, *, reason=None):
        payload = { 'code': code }
        return self.request(Route('PATCH', '/guilds/{guild_id}/vanity-url', guild_id=guild_id), json=payload, reason=reason)

    def prune_members(self, guild_id, days, *, reason=None):
        params = {
            'days': days
        }
        return self.request(Route('POST', '/guilds/{guild_id}/prune', guild_id=guild_id), params=params, reason=reason)

    def estimate_pruned_members(self, guild_id, days):
        params = {
            'days': days
        }
        return self.request(Route('GET', '/guilds/{guild_id}/prune', guild_id=guild_id), params=params)

    def create_custom_emoji(self, guild_id, name, image, *, reason=None):
        payload = {
            'name': name,
            'image': image
        }

        r = Route('POST', '/guilds/{guild_id}/emojis', guild_id=guild_id)
        return self.request(r, json=payload, reason=reason)

    def delete_custom_emoji(self, guild_id, emoji_id, *, reason=None):
        r = Route('DELETE', '/guilds/{guild_id}/emojis/{emoji_id}', guild_id=guild_id, emoji_id=emoji_id)
        return self.request(r, reason=reason)

    def edit_custom_emoji(self, guild_id, emoji_id, *, name, reason=None):
        payload = {
            'name': name
        }
        r = Route('PATCH', '/guilds/{guild_id}/emojis/{emoji_id}', guild_id=guild_id, emoji_id=emoji_id)
        return self.request(r, json=payload, reason=reason)

    def get_audit_logs(self, guild_id, limit=100, before=None, after=None, user_id=None, action_type=None):
        params = { 'limit': limit }
        if before:
            params['before'] = before
        if after:
            params['after'] = after
        if user_id:
            params['user_id'] = user_id
        if action_type:
            params['action_type'] = action_type

        r = Route('GET', '/guilds/{guild_id}/audit-logs', guild_id=guild_id)
        return self.request(r, params=params)

    # Invite management

    def create_invite(self, channel, *, reason=None, **options):
        r = Route('POST', '/channels/{channel.id}/invites', channel=channel)
        payload = {
            'max_age': options.get('max_age', 0),
            'max_uses': options.get('max_uses', 0),
            'temporary': options.get('temporary', False),
            'unique': options.get('unique', True)
        }

        return self.request(r, reason=reason, json=payload)

    def get_invite(self, invite_id):
        return self.request(Route('GET', '/invite/{invite_id}', invite_id=invite_id))

    def invites_from(self, guild_id):
        return self.request(Route('GET', '/guilds/{guild_id}/invites', guild_id=guild_id))

    def invites_from_channel(self, channel):
        return self.request(Route('GET', '/channels/{channel.id}/invites', channel=channel))

    def delete_invite(self, invite_id, *, reason=None):
        return self.request(Route('DELETE', '/invite/{invite_id}', invite_id=invite_id), reason=reason)

    # Role management

    def edit_role(self, guild_id, role_id, *, reason=None, **fields):
        r = Route('PATCH', '/guilds/{guild_id}/roles/{role_id}', guild_id=guild_id, role_id=role_id)
        valid_keys = ('name', 'permissions', 'color', 'hoist', 'mentionable')
        payload = {
            k: v for k, v in fields.items() if k in valid_keys
        }
        return self.request(r, json=payload, reason=reason)

    def delete_role(self, guild_id, role_id, *, reason=None):
        r = Route('DELETE', '/guilds/{guild_id}/roles/{role_id}', guild_id=guild_id, role_id=role_id)
        return self.request(r, reason=reason)

    def replace_roles(self, user_id, guild_id, role_ids, *, reason=None):
        return self.edit_member(guild_id=guild_id, user_id=user_id, roles=role_ids, reason=reason)

    def create_role(self, guild_id, *, reason=None, **fields):
        r = Route('POST', '/guilds/{guild_id}/roles', guild_id=guild_id)
        return self.request(r, json=fields, reason=reason)

    def move_role_position(self, guild_id, positions, *, reason=None):
        r = Route('PATCH', '/guilds/{guild_id}/roles', guild_id=guild_id)
        return self.request(r, json=positions, reason=reason)

    def add_role(self, guild_id, user_id, role_id, *, reason=None):
        r = Route('PUT', '/guilds/{guild_id}/members/{user_id}/roles/{role_id}',
                  guild_id=guild_id, user_id=user_id, role_id=role_id)
        return self.request(r, reason=reason)

    def remove_role(self, guild_id, user_id, role_id, *, reason=None):
        r = Route('DELETE', '/guilds/{guild_id}/members/{user_id}/roles/{role_id}',
                  guild_id=guild_id, user_id=user_id, role_id=role_id)
        return self.request(r, reason=reason)

    def edit_channel_permissions(self, channel, target, allow, deny, type, *, reason=None):
        payload = {
            'id': target,
            'allow': allow,
            'deny': deny,
            'type': type
        }
        r = Route('PUT', '/channels/{channel.id}/permissions/{target}', channel=channel, target=target)
        return self.request(r, json=payload, reason=reason)

    def delete_channel_permissions(self, channel, target, *, reason=None):
        r = Route('DELETE', '/channels/{channel.id}/permissions/{target}', channel=channel, target=target)
        return self.request(r, reason=reason)

    # Voice management

    def move_member(self, user_id, guild_id, channel, *, reason=None):
        return self.edit_member(guild_id=guild_id, user_id=user_id, channel=channel, reason=reason)

    # Relationship related

    def remove_relationship(self, user_id):
        r = Route('DELETE', '/users/@me/relationships/{user_id}', user_id=user_id)
        return self.request(r)

    def add_relationship(self, user_id, type=None):
        r = Route('PUT', '/users/@me/relationships/{user_id}', user_id=user_id)
        payload = {}
        if type is not None:
            payload['type'] = type

        return self.request(r, json=payload)

    def send_friend_request(self, username, discriminator):
        r = Route('POST', '/users/@me/relationships')
        payload = {
            'username': username,
            'discriminator': int(discriminator)
        }
        return self.request(r, json=payload)

    # Misc

    def application_info(self):
        return self.request(Route('GET', '/oauth2/applications/@me'))

    @asyncio.coroutine
    def get_gateway(self, *, encoding='json', v=6, zlib=True):
        try:
            data = yield from self.request(Route('GET', '/gateway'))
        except HTTPException as e:
            raise GatewayNotFound() from e
        if zlib:
            value = '{0}?encoding={1}&v={2}&compress=zlib-stream'
        else:
            value = '{0}?encoding={1}&v={2}'
        return value.format(data['url'], encoding, v)

    @asyncio.coroutine
    def get_bot_gateway(self, *, encoding='json', v=6, zlib=True):
        try:
            data = yield from self.request(Route('GET', '/gateway/bot'))
        except HTTPException as e:
            raise GatewayNotFound() from e

        if zlib:
            value = '{0}?encoding={1}&v={2}&compress=zlib-stream'
        else:
            value = '{0}?encoding={1}&v={2}'
        return data['shards'], value.format(data['url'], encoding, v)

    def get_user_info(self, user_id):
        return self.request(Route('GET', '/users/{user_id}', user_id=user_id))

    def get_user_profile(self, user_id):
        return self.request(Route('GET', '/users/{user_id}/profile', user_id=user_id))
