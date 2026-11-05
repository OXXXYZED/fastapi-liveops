class LoginResult {
  final String playerId;
  final String accessToken;
  final int expiresIn;
  final bool created;

  LoginResult.fromJson(Map<String, dynamic> j)
      : playerId = j['player_id'] as String,
        accessToken = j['access_token'] as String,
        expiresIn = j['expires_in'] as int,
        created = j['created'] as bool;
}

class RemoteConfig {
  final int version;
  final Map<String, dynamic> values;
  final Map<String, bool> flags;

  const RemoteConfig({this.version = 0, this.values = const {}, this.flags = const {}});

  static const empty = RemoteConfig();

  factory RemoteConfig.fromJson(Map<String, dynamic> j) => RemoteConfig(
        version: j['version'] as int? ?? 0,
        values: (j['values'] as Map<String, dynamic>?) ?? const {},
        flags: ((j['flags'] as Map<String, dynamic>?) ?? const {})
            .map((k, v) => MapEntry(k, v == true)),
      );

  bool isEnabled(String flag) => flags[flag] ?? false;

  /// Reads a value by key; dots address nested objects (`shop.discount`).
  /// Returns [fallback] if missing or of a different type. Ints widen to double.
  T get<T>(String key, T fallback) {
    Object? node = values;
    for (final part in key.split('.')) {
      if (node is! Map<String, dynamic> || !node.containsKey(part)) return fallback;
      node = node[part];
    }
    if (node is T) return node;
    if (node is int && fallback is double) return node.toDouble() as T;
    return fallback;
  }
}

class LeaderboardEntry {
  final int rank;
  final String playerId;
  final int score;

  LeaderboardEntry.fromJson(Map<String, dynamic> j)
      : rank = j['rank'] as int,
        playerId = j['player_id'] as String,
        score = j['score'] as int;
}

List<LeaderboardEntry> _entries(Object? raw) => ((raw as List<dynamic>?) ?? const [])
    .map((e) => LeaderboardEntry.fromJson(e as Map<String, dynamic>))
    .toList();

class LeaderboardPage {
  final String key;
  final String season;
  final int total;
  final List<LeaderboardEntry> entries;

  LeaderboardPage.fromJson(Map<String, dynamic> j)
      : key = j['key'] as String,
        season = j['season'] as String,
        total = j['total'] as int,
        entries = _entries(j['entries']);
}

class MyRank {
  final String key;
  final String season;
  final int total;
  final LeaderboardEntry? me;
  final List<LeaderboardEntry> around;

  MyRank.fromJson(Map<String, dynamic> j)
      : key = j['key'] as String,
        season = j['season'] as String,
        total = j['total'] as int,
        me = j['me'] == null ? null : LeaderboardEntry.fromJson(j['me'] as Map<String, dynamic>),
        around = _entries(j['around']);
}

class ScoreResult {
  final String season;
  final int score;
  final int rank;

  ScoreResult.fromJson(Map<String, dynamic> j)
      : season = j['season'] as String,
        score = j['score'] as int,
        rank = j['rank'] as int;
}

class SaveInfo {
  final String slot;
  final int version;
  final int sizeBytes;
  final DateTime updatedAt;

  SaveInfo.fromJson(Map<String, dynamic> j)
      : slot = j['slot'] as String,
        version = j['version'] as int,
        sizeBytes = j['size_bytes'] as int,
        updatedAt = DateTime.parse(j['updated_at'] as String);
}

class SaveData extends SaveInfo {
  /// Decoded JSON (Map, List, num, String, bool or null).
  final Object? data;

  SaveData.fromJson(super.j)
      : data = j['data'],
        super.fromJson();
}
