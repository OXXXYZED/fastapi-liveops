/// Persistent key-value storage for tokens, install id and the offline config cache.
///
/// In Flutter, back it with `shared_preferences` (see README). Methods may be async.
abstract interface class KeyValueStore {
  Future<String?> get(String key);
  Future<void> set(String key, String value);
  Future<void> delete(String key);
}

class MemoryStore implements KeyValueStore {
  final Map<String, String> data = {};

  @override
  Future<String?> get(String key) async => data[key];

  @override
  Future<void> set(String key, String value) async => data[key] = value;

  @override
  Future<void> delete(String key) async => data.remove(key);
}
