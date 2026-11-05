import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:http/http.dart' as http;

import 'errors.dart';
import 'models.dart';
import 'store.dart';

/// Entry point of the SDK. Create one per environment and keep it for the app lifetime.
class LiveOpsClient {
  static const _refreshMargin = Duration(seconds: 60);

  final Uri _base;
  final String apiKey;
  final String? _deviceId;
  final KeyValueStore store;
  final String storePrefix;
  final http.Client _http;
  final Duration timeout;
  final DateTime Function() _now;

  /// Targeting attributes sent with config requests (platform, app_version, ...).
  final Map<String, String> attributes;

  RemoteConfig _config = RemoteConfig.empty;
  final _configController = StreamController<RemoteConfig>.broadcast();
  Future<LoginResult>? _loginInFlight;

  // Cached in memory; mirrored to [store].
  String? _token;
  DateTime? _tokenExp;
  String? _playerId;

  late final LeaderboardsApi leaderboards = LeaderboardsApi._(this);
  late final CloudSaveApi cloudSave = CloudSaveApi._(this);

  LiveOpsClient({
    required String baseUrl,
    required this.apiKey,
    String? deviceId,
    KeyValueStore? store,
    this.storePrefix = 'liveops.',
    Map<String, String>? attributes,
    http.Client? httpClient,
    this.timeout = const Duration(seconds: 15),
    DateTime Function()? now,
  })  : _base =
            Uri.parse(baseUrl.endsWith('/') ? baseUrl.substring(0, baseUrl.length - 1) : baseUrl),
        _deviceId = deviceId,
        store = store ?? MemoryStore(),
        attributes = attributes ?? {},
        _http = httpClient ?? http.Client(),
        _now = now ?? DateTime.now {
    if (apiKey.isEmpty) throw ArgumentError.value(apiKey, 'apiKey', 'required');
  }

  String _k(String name) => '$storePrefix$name';

  /// Loads persisted session and cached config. Call once at startup before reading [config].
  Future<void> init() async {
    _token = await store.get(_k('token'));
    final exp = await store.get(_k('token_exp'));
    _tokenExp = exp == null ? null : DateTime.tryParse(exp);
    _playerId = await store.get(_k('player_id'));
    final cached = await store.get(_k('config'));
    if (cached != null) {
      try {
        _config = RemoteConfig.fromJson(jsonDecode(cached) as Map<String, dynamic>);
      } on Object {
        await store.delete(_k('config'));
      }
    }
  }

  String? get playerId => _playerId;

  /// Last known config (fetched or from the offline cache). Never null.
  RemoteConfig get config => _config;

  /// Emits whenever [config] changes after a fetch.
  Stream<RemoteConfig> get configUpdates => _configController.stream;

  bool get hasValidToken =>
      _token != null && _tokenExp != null && _now().add(_refreshMargin).isBefore(_tokenExp!);

  Future<String> deviceId() async {
    if (_deviceId != null && _deviceId.isNotEmpty) return _deviceId;
    var id = await store.get(_k('install_id'));
    if (id == null) {
      id = _randomId();
      await store.set(_k('install_id'), id);
    }
    return id;
  }

  // ---------------- auth ----------------

  /// Logs in (or resumes the player bound to [deviceId]). Concurrent calls share one request.
  Future<LoginResult> login() =>
      _loginInFlight ??= _doLogin().whenComplete(() => _loginInFlight = null);

  Future<LoginResult> _doLogin() async =>
      _apiKeyLogin('/v1/auth/anonymous', {'device_id': await deviceId()});

  /// Signs in with a Google/Apple ID token or a Steam session ticket. Resumes the player that has
  /// this account linked, or creates a new one. Subsequent calls use this session.
  Future<LoginResult> loginWithProvider(String provider, String token) =>
      _apiKeyLogin('/v1/auth/${Uri.encodeComponent(provider)}', {'token': token});

  Future<LoginResult> _apiKeyLogin(String path, Map<String, String> body) async {
    final res = await _send('POST', path, headers: {'X-Api-Key': apiKey}, body: jsonEncode(body));
    _ensureSuccess(res);
    final r = LoginResult.fromJson(_json(res) as Map<String, dynamic>);
    _token = r.accessToken;
    _tokenExp = _now().toUtc().add(Duration(seconds: r.expiresIn));
    _playerId = r.playerId;
    await store.set(_k('token'), r.accessToken);
    await store.set(_k('token_exp'), _tokenExp!.toIso8601String());
    await store.set(_k('player_id'), r.playerId);
    return r;
  }

  Future<void> logout() async {
    _token = _tokenExp = _playerId = null;
    for (final k in ['token', 'token_exp', 'player_id']) {
      await store.delete(_k(k));
    }
  }

  // ---------------- profile ----------------

  Future<PlayerProfile> getProfile() async =>
      PlayerProfile.fromJson(await _playerJson('GET', '/v1/players/me') as Map<String, dynamic>);

  /// 3–24 letters, digits, space, `. _ -`; null clears it.
  Future<PlayerProfile> setDisplayName(String? name) async => PlayerProfile.fromJson(
      await _playerJson('PATCH', '/v1/players/me', body: {'display_name': name})
          as Map<String, dynamic>);

  /// Links a Google/Apple/Steam account to the current player so progress follows it to other
  /// devices. Throws [LiveOpsException] with status 409 if it belongs to another player.
  Future<PlayerProfile> linkIdentity(String provider, String token) async => PlayerProfile.fromJson(
      await _playerJson('POST', '/v1/players/me/identities/${Uri.encodeComponent(provider)}',
          body: {'token': token}) as Map<String, dynamic>);

  Future<PlayerProfile> unlinkIdentity(String provider) async => PlayerProfile.fromJson(
      await _playerJson('DELETE', '/v1/players/me/identities/${Uri.encodeComponent(provider)}')
          as Map<String, dynamic>);

  // ---------------- remote config ----------------

  /// Fetches config + evaluated flags with ETag caching. When the server is unreachable,
  /// returns the cached config instead of throwing.
  Future<RemoteConfig> fetchConfig() async {
    final keys = attributes.keys.toList()..sort();
    final query = keys
        .map((k) => '${Uri.encodeQueryComponent(k)}=${Uri.encodeQueryComponent(attributes[k]!)}')
        .join('&');
    final path = '/v1/config${query.isEmpty ? '' : '?$query'}';
    // The ETag is only valid together with the attributes it was computed for.
    final etag =
        await store.get(_k('config_attrs')) == query ? await store.get(_k('config_etag')) : null;

    final http.Response res;
    try {
      res = await playerRequest('GET', path, ifNoneMatch: etag);
    } on NetworkException {
      return _config;
    }
    if (res.statusCode == 304) return _config;
    _ensureSuccess(res);
    final cfg = RemoteConfig.fromJson(_json(res) as Map<String, dynamic>);
    await store.set(_k('config'), res.body);
    await store.set(_k('config_attrs'), query);
    final newEtag = res.headers['etag'];
    if (newEtag != null) {
      await store.set(_k('config_etag'), newEtag);
    } else {
      await store.delete(_k('config_etag'));
    }
    _config = cfg;
    _configController.add(cfg);
    return cfg;
  }

  void close() {
    _configController.close();
    _http.close();
  }

  // ---------------- plumbing ----------------

  /// Sends an authenticated request, logging in first and once more on 401.
  Future<http.Response> playerRequest(String method, String path,
      {Object? body, String? ifNoneMatch}) async {
    if (!hasValidToken) await login();
    for (var attempt = 0;; attempt++) {
      final res = await _send(method, path,
          headers: {
            'Authorization': 'Bearer $_token',
            if (ifNoneMatch != null) 'If-None-Match': ifNoneMatch,
          },
          body: body == null ? null : jsonEncode(body));
      if (res.statusCode == 401 && attempt == 0) {
        await login();
        continue;
      }
      return res;
    }
  }

  Future<Object?> _playerJson(String method, String path, {Object? body}) async {
    final res = await playerRequest(method, path, body: body);
    _ensureSuccess(res);
    return res.body.isEmpty ? null : _json(res);
  }

  Future<http.Response> _send(String method, String path,
      {Map<String, String> headers = const {}, String? body}) async {
    final uri = Uri.parse('$_base$path');
    final req = http.Request(method, uri)
      ..headers['Accept'] = 'application/json'
      ..headers.addAll(headers);
    if (body != null) {
      req.headers['Content-Type'] = 'application/json';
      req.body = body;
    }
    try {
      return await http.Response.fromStream(await _http.send(req).timeout(timeout));
    } on http.ClientException catch (e) {
      throw NetworkException(e);
    } on TimeoutException catch (e) {
      throw NetworkException(e);
    }
  }

  static Object? _json(http.Response res) => jsonDecode(utf8.decode(res.bodyBytes));

  static void _ensureSuccess(http.Response res) {
    final s = res.statusCode;
    if (s >= 200 && s < 300) return;
    if (s == 429) {
      final ra = int.tryParse(res.headers['retry-after'] ?? '') ?? 1;
      throw RateLimitedException(Duration(seconds: ra), res.body);
    }
    throw LiveOpsException(s, 'HTTP $s: ${_detail(res.body)}', res.body);
  }

  static String _detail(String body) {
    try {
      final d = (jsonDecode(body) as Map<String, dynamic>)['detail'];
      if (d == null) return body;
      return d is String ? d : jsonEncode(d);
    } on Object {
      return body;
    }
  }

  static String _randomId() {
    Random r;
    try {
      r = Random.secure();
    } on UnsupportedError {
      r = Random();
    }
    return List.generate(16, (_) => r.nextInt(256).toRadixString(16).padLeft(2, '0')).join();
  }
}

class LeaderboardsApi {
  final LiveOpsClient _c;
  LeaderboardsApi._(this._c);

  String _p(String board) => '/v1/leaderboards/${Uri.encodeComponent(board)}';

  Future<ScoreResult> submitScore(String board, int score) async => ScoreResult.fromJson(await _c
      ._playerJson('POST', '${_p(board)}/scores', body: {'score': score}) as Map<String, dynamic>);

  /// [season]: `current`, `previous` or an explicit id like `2026-W45`.
  Future<LeaderboardPage> getTop(String board,
          {int limit = 10, int offset = 0, String season = 'current'}) async =>
      LeaderboardPage.fromJson(await _c._playerJson('GET',
              '${_p(board)}?limit=$limit&offset=$offset&season=${Uri.encodeQueryComponent(season)}')
          as Map<String, dynamic>);

  Future<MyRank> getAroundMe(String board, {int radius = 5, String season = 'current'}) async =>
      MyRank.fromJson(await _c._playerJson(
              'GET', '${_p(board)}/me?radius=$radius&season=${Uri.encodeQueryComponent(season)}')
          as Map<String, dynamic>);
}

class CloudSaveApi {
  final LiveOpsClient _c;
  CloudSaveApi._(this._c);

  String _p(String slot) => '/v1/saves/${Uri.encodeComponent(slot)}';

  Future<List<SaveInfo>> list() async => (await _c._playerJson('GET', '/v1/saves') as List<dynamic>)
      .map((e) => SaveInfo.fromJson(e as Map<String, dynamic>))
      .toList();

  /// Loads a slot, or returns null if it doesn't exist.
  Future<SaveData?> load(String slot) async {
    final res = await _c.playerRequest('GET', _p(slot));
    if (res.statusCode == 404) return null;
    LiveOpsClient._ensureSuccess(res);
    return SaveData.fromJson(LiveOpsClient._json(res) as Map<String, dynamic>);
  }

  /// Writes a slot. [expectedVersion]: 0 = create only, N = only if the server still has
  /// version N, null = overwrite. Throws [SaveConflictException] on mismatch.
  Future<SaveInfo> save(String slot, Object? data, {int? expectedVersion}) async {
    final res = await _c.playerRequest('PUT', _p(slot),
        body: {'data': data, if (expectedVersion != null) 'expected_version': expectedVersion});
    _throwIfConflict(res);
    LiveOpsClient._ensureSuccess(res);
    return SaveInfo.fromJson(LiveOpsClient._json(res) as Map<String, dynamic>);
  }

  /// Returns false if the slot did not exist.
  Future<bool> delete(String slot, {int? expectedVersion}) async {
    final q = expectedVersion == null ? '' : '?expected_version=$expectedVersion';
    final res = await _c.playerRequest('DELETE', '${_p(slot)}$q');
    if (res.statusCode == 404) return false;
    _throwIfConflict(res);
    LiveOpsClient._ensureSuccess(res);
    return true;
  }

  static void _throwIfConflict(http.Response res) {
    if (res.statusCode != 409) return;
    try {
      final detail = (jsonDecode(res.body) as Map<String, dynamic>)['detail'];
      if (detail is Map<String, dynamic> && detail['current_version'] is int) {
        throw SaveConflictException(detail['current_version'] as int, res.body);
      }
    } on FormatException {
      // fall through to generic error
    }
  }
}
