import 'dart:collection';
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:liveops_client/liveops_client.dart';
import 'package:test/test.dart';

typedef Handler = http.Response Function(http.Request req);

class Fake {
  final requests = <http.Request>[];
  final _queue = Queue<Handler>();

  late final client = MockClient((req) async {
    requests.add(req);
    if (_queue.isEmpty) throw StateError('unexpected ${req.method} ${req.url}');
    return _queue.removeFirst()(req);
  });

  Fake enqueue(int status, [Object? body, Map<String, String> headers = const {}]) {
    _queue.add((_) => http.Response(
        body == null ? '' : (body is String ? body : jsonEncode(body)), status,
        headers: headers));
    return this;
  }

  Fake networkError() {
    _queue.add((_) => throw http.ClientException('offline'));
    return this;
  }

  Fake login({String token = 'tok', String playerId = 'p1', int expiresIn = 3600}) => enqueue(200,
      {'player_id': playerId, 'access_token': token, 'expires_in': expiresIn, 'created': true});
}

void main() {
  late Fake fake;
  late MemoryStore store;
  late DateTime now;

  LiveOpsClient make({String? deviceId, Map<String, String>? attrs}) => LiveOpsClient(
        baseUrl: 'https://api.test/',
        apiKey: 'lo_client_x',
        deviceId: deviceId,
        store: store,
        attributes: attrs,
        httpClient: fake.client,
        now: () => now,
      );

  setUp(() {
    fake = Fake();
    store = MemoryStore();
    now = DateTime.utc(2026, 1, 1);
  });

  test('login persists session and generated install id', () async {
    final c = make();
    fake.login();
    final r = await c.login();
    expect(r.playerId, 'p1');
    expect(c.playerId, 'p1');
    expect(c.hasValidToken, isTrue);
    final req = fake.requests.single;
    expect(req.url.toString(), 'https://api.test/v1/auth/anonymous');
    expect(req.headers['X-Api-Key'], 'lo_client_x');
    final id = store.data['liveops.install_id']!;
    expect(jsonDecode(req.body), {'device_id': id});

    final c2 = make();
    await c2.init();
    expect(c2.playerId, 'p1');
    expect(c2.hasValidToken, isTrue);
    expect(await c2.deviceId(), id);
  });

  test('explicit device id', () async {
    fake.login();
    await make(deviceId: 'my-device-123').login();
    expect(fake.requests.single.body, contains('my-device-123'));
  });

  test('auto login and refresh near expiry', () async {
    final c = make();
    fake.login(token: 't1', expiresIn: 120).enqueue(200, []);
    await c.cloudSave.list();
    expect(fake.requests[1].headers['Authorization'], 'Bearer t1');

    now = now.add(const Duration(seconds: 90));
    fake.login(token: 't2').enqueue(200, []);
    await c.cloudSave.list();
    expect(fake.requests, hasLength(4));
    expect(fake.requests[3].headers['Authorization'], 'Bearer t2');
  });

  test('401 re-logs in once', () async {
    final c = make();
    fake.login(token: 't1').enqueue(401).login(token: 't2').enqueue(200, []);
    await c.cloudSave.list();
    expect(fake.requests[3].headers['Authorization'], 'Bearer t2');

    fake.enqueue(401).login(token: 't3').enqueue(401);
    await expectLater(c.cloudSave.list(),
        throwsA(isA<LiveOpsException>().having((e) => e.status, 'status', 401)));
  });

  test('concurrent calls share one login', () async {
    final c = make();
    fake.login().enqueue(200, []).enqueue(200, []);
    await Future.wait([c.cloudSave.list(), c.cloudSave.list()]);
    expect(fake.requests.where((r) => r.url.path == '/v1/auth/anonymous'), hasLength(1));
  });

  test('config: fetch, etag, offline fallback, cache on init', () async {
    final c = make(attrs: {'platform': 'ios'});
    final updates = <RemoteConfig>[];
    c.configUpdates.listen(updates.add);

    fake.login().enqueue(200, {
      'version': 2,
      'values': {
        'lives': 3,
        'shop': {'discount': 0.25, 'tiers': 2}
      },
      'flags': {'new_shop': true}
    }, {
      'etag': '"abc"'
    });
    final cfg = await c.fetchConfig();
    expect(fake.requests[1].url.toString(), 'https://api.test/v1/config?platform=ios');
    expect(cfg.get('lives', 0), 3);
    expect(cfg.get('shop.discount', 0.0), 0.25);
    expect(cfg.get('shop.tiers', 0.0), 2.0); // int widens to double
    expect(cfg.get('shop', 7), 7);
    expect(cfg.get('missing.key', 'def'), 'def');
    expect(cfg.isEnabled('new_shop'), isTrue);
    expect(cfg.isEnabled('nope'), isFalse);

    fake.enqueue(304);
    expect(identical(await c.fetchConfig(), cfg), isTrue);
    expect(fake.requests[2].headers['If-None-Match'], '"abc"');

    fake.networkError();
    expect((await c.fetchConfig()).version, 2);

    await Future<void>.delayed(Duration.zero);
    expect(updates, hasLength(1));

    final c2 = make();
    await c2.init();
    expect(c2.config.version, 2);
    expect(c2.config.isEnabled('new_shop'), isTrue);
  });

  test('config: etag dropped when attributes change', () async {
    final attrs = {'platform': 'ios'};
    final c = make(attrs: attrs);
    fake.login().enqueue(200, {'version': 1, 'values': {}, 'flags': {}}, {'etag': '"e1"'});
    await c.fetchConfig();
    attrs['app_version'] = '1.2.0';
    fake.enqueue(200, {'version': 1, 'values': {}, 'flags': {}});
    await c.fetchConfig();
    expect(fake.requests[2].headers.containsKey('If-None-Match'), isFalse);
    expect(fake.requests[2].url.query, 'app_version=1.2.0&platform=ios');
  });

  test('errors are mapped', () async {
    final c = make();
    fake.login().enqueue(429, {'detail': 'rate limit exceeded'}, {'retry-after': '12'});
    await expectLater(c.leaderboards.getTop('main'),
        throwsA(isA<RateLimitedException>().having((e) => e.retryAfter.inSeconds, 'retry', 12)));

    fake.enqueue(404, {'detail': 'leaderboard not found'});
    await expectLater(
        c.leaderboards.getTop('x'),
        throwsA(isA<LiveOpsException>()
            .having((e) => e.status, 'status', 404)
            .having((e) => e.message, 'message', contains('leaderboard not found'))));

    fake.networkError();
    await expectLater(c.leaderboards.getTop('main'), throwsA(isA<NetworkException>()));
  });

  test('leaderboards', () async {
    final c = make();
    fake.login().enqueue(200, {'season': 'all', 'score': 500, 'rank': 1});
    expect((await c.leaderboards.submitScore('main', 500)).score, 500);
    expect(jsonDecode(fake.requests[1].body), {'score': 500});
    fake.enqueue(200, {'season': 'all', 'score': 7, 'rank': 1});
    await c.leaderboards.submitScore('main', 7, metadata: {'match': 'm1'});
    expect(jsonDecode(fake.requests[2].body), {
      'score': 7,
      'metadata': {'match': 'm1'}
    });
    fake.requests.removeAt(2);

    fake.enqueue(200, {
      'key': 'main',
      'season': '2026-W01',
      'total': 1,
      'entries': [
        {'rank': 1, 'player_id': 'p1', 'score': 500}
      ]
    });
    final page = await c.leaderboards.getTop('main', limit: 5, offset: 10, season: 'previous');
    expect(fake.requests[2].url.toString(),
        'https://api.test/v1/leaderboards/main?limit=5&offset=10&season=previous');
    expect(page.entries.single.score, 500);

    fake.enqueue(200, {'key': 'main', 'season': 'all', 'total': 0, 'me': null, 'around': []});
    expect((await c.leaderboards.getAroundMe('main', radius: 2)).me, isNull);
  });

  test('cloud save', () async {
    final c = make();
    const meta = {
      'slot': 'main',
      'version': 1,
      'size_bytes': 10,
      'created_at': '2026-01-01T00:00:00Z',
      'updated_at': '2026-01-01T00:00:00Z'
    };
    fake.login().enqueue(404, {'detail': 'save not found'});
    expect(await c.cloudSave.load('main'), isNull);

    fake.enqueue(200, meta);
    expect((await c.cloudSave.save('main', {'level': 3}, expectedVersion: 0)).version, 1);
    expect(fake.requests[2].method, 'PUT');
    expect(jsonDecode(fake.requests[2].body), {
      'data': {'level': 3},
      'expected_version': 0
    });

    fake.enqueue(200, {
      ...meta,
      'data': {'level': 3}
    });
    expect((await c.cloudSave.load('main'))!.data, {'level': 3});

    fake.enqueue(409, {
      'detail': {'message': 'version mismatch', 'current_version': 4}
    });
    await expectLater(c.cloudSave.save('main', 1, expectedVersion: 1),
        throwsA(isA<SaveConflictException>().having((e) => e.currentVersion, 'v', 4)));

    fake.enqueue(409, {
      'detail': {'message': 'slot limit (10) reached'}
    });
    await expectLater(c.cloudSave.save('x', 1),
        throwsA(allOf(isA<LiveOpsException>(), isNot(isA<SaveConflictException>()))));

    fake.enqueue(204).enqueue(404);
    expect(await c.cloudSave.delete('main', expectedVersion: 1), isTrue);
    expect(fake.requests.last.url.query, 'expected_version=1');
    expect(await c.cloudSave.delete('main'), isFalse);
  });

  test('provider login, profile and linking', () async {
    final c = make();
    fake.login(token: 'tg', playerId: 'p9');
    await c.loginWithProvider('google', 'id-token');
    expect(c.playerId, 'p9');
    expect(fake.requests[0].url.path, '/v1/auth/google');
    expect(jsonDecode(fake.requests[0].body), {'token': 'id-token'});

    const profile = {
      'id': 'p9',
      'environment_id': 'e',
      'display_name': 'Neo',
      'created_at': '2026-01-01T00:00:00Z',
      'last_seen_at': '2026-01-01T00:00:00Z',
      'identities': [
        {'provider': 'google', 'email': 'a@b.c', 'created_at': '2026-01-01T00:00:00Z'}
      ]
    };
    fake.enqueue(200, profile);
    final p = await c.setDisplayName('Neo');
    expect(fake.requests[1].method, 'PATCH');
    expect(fake.requests[1].headers['Authorization'], 'Bearer tg');
    expect(p.displayName, 'Neo');
    expect(p.identities.single.provider, 'google');

    fake.enqueue(409, {
      'detail': {'message': 'account already linked to another player', 'code': 'identity_in_use'}
    });
    await expectLater(c.linkIdentity('apple', 'x'),
        throwsA(isA<LiveOpsException>().having((e) => e.status, 'status', 409)));

    fake.enqueue(200, profile);
    await c.unlinkIdentity('google');
    expect(fake.requests[3].method, 'DELETE');
  });

  test('experiments and track', () async {
    final c = make();
    fake.login().enqueue(200, {
      'version': 1,
      'values': {'price': 5},
      'flags': {},
      'experiments': {'shop': 'cheap'}
    });
    final cfg = await c.fetchConfig();
    expect(cfg.variant('shop'), 'cheap');
    expect(cfg.variant('other'), isNull);

    fake.enqueue(202, {'accepted': 1});
    await c.track('level_complete', {'level': 3});
    final body = jsonDecode(fake.requests[2].body) as Map<String, dynamic>;
    final e = (body['events'] as List<dynamic>).single as Map<String, dynamic>;
    expect(e['name'], 'level_complete');
    expect(e['properties'], {'level': 3});
    expect(e['ts'], '2026-01-01T00:00:00.000Z');
  });
}
