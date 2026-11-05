# liveops_client

Dart / Flutter client for [fastapi-liveops](https://github.com/OXXXYZED/fastapi-liveops).
Pure Dart (only depends on `package:http`), so it works in Flutter on every platform and in
server-side Dart.

## Install

```yaml
dependencies:
  liveops_client:
    git:
      url: https://github.com/OXXXYZED/fastapi-liveops.git
      path: sdk/dart/liveops_client
```

## Usage

```dart
final liveops = LiveOpsClient(
  baseUrl: 'https://liveops.example.com',
  apiKey: 'lo_client_...',
  store: SharedPrefsStore(await SharedPreferences.getInstance()), // see below
  attributes: {'platform': Platform.operatingSystem, 'app_version': '1.4.0'},
);
await liveops.init();                       // loads session + cached config

applyConfig(liveops.config);                // cached config, available offline
liveops.configUpdates.listen(applyConfig);
await liveops.fetchConfig();                // never throws on network errors

final lives = liveops.config.get('lives', 3);
final discount = liveops.config.get('shop.discount', 0.0);
if (liveops.config.isEnabled('new_shop')) { /* ... */ }
final variant = liveops.config.variant('shop_price');   // A/B experiment
await liveops.track('shop_opened', {'variant': variant});

await liveops.leaderboards.submitScore('main', 1200);
final top = await liveops.leaderboards.getTop('main', limit: 20);
final me = await liveops.leaderboards.getAroundMe('main', radius: 3);

// Optional: keep progress across devices.
// await liveops.linkIdentity('google', googleIdToken);  // or loginWithProvider on a new device
await liveops.setDisplayName('Neo');

final save = await liveops.cloudSave.load('main');
try {
  await liveops.cloudSave.save('main', {'level': 4}, expectedVersion: save?.version ?? 0);
} on SaveConflictException catch (e) {
  // Another device wrote first: reload, merge, retry with e.currentVersion.
}
```

### Persisting with shared_preferences

The package doesn't depend on Flutter; add this adapter in your app:

```dart
class SharedPrefsStore implements KeyValueStore {
  SharedPrefsStore(this.prefs);
  final SharedPreferences prefs;
  @override Future<String?> get(String key) async => prefs.getString(key);
  @override Future<void> set(String key, String value) => prefs.setString(key, value);
  @override Future<void> delete(String key) => prefs.remove(key);
}
```

Use `flutter_secure_storage` instead if you want the access token encrypted at rest.

## Behaviour

- **Auth** — logs in lazily using a persisted install id (or `deviceId`), refreshes the token
  60 s before expiry, re-logs in once on `401`. Concurrent calls share one login.
- **Errors** — `LiveOpsException` (`status`, `responseBody`), `NetworkException`,
  `RateLimitedException` (`retryAfter`), `SaveConflictException` (`currentVersion`).
- API mirrors the Unity SDK.

## Development

```bash
dart pub get && dart analyze && dart test
```
