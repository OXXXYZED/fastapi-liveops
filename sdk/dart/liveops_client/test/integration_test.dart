import 'dart:io';

import 'package:liveops_client/liveops_client.dart';
import 'package:test/test.dart';

/// Runs against a real server when LIVEOPS_TEST_URL and LIVEOPS_TEST_API_KEY are set
/// (see scripts/seed_integration.sh and .github/workflows/ci.yml).
void main() {
  final url = Platform.environment['LIVEOPS_TEST_URL'];
  final key = Platform.environment['LIVEOPS_TEST_API_KEY'];
  final skip = (url == null || key == null) ? 'integration env not set' : null;

  LiveOpsClient client(String device) => LiveOpsClient(
      baseUrl: url!, apiKey: key!, deviceId: device, attributes: {'platform': 'android'});

  test('end to end', () async {
    final device = 'it-dart-${DateTime.now().microsecondsSinceEpoch}';
    final c = client(device);
    final login = await c.login();
    expect(login.created, isTrue);
    expect((await client(device).login()).created, isFalse);

    final cfg = await c.fetchConfig();
    expect(identical(await c.fetchConfig(), cfg), isTrue); // 304

    expect((await c.setDisplayName('Dart Tester')).displayName, 'Dart Tester');
    expect((await c.leaderboards.submitScore('it-main', 7)).score, 7);
    final me = (await c.leaderboards.getAroundMe('it-main')).me!;
    expect(me.playerId, login.playerId);
    expect(me.displayName, 'Dart Tester');

    final saved = await c.cloudSave.save('main', {'gold': 5}, expectedVersion: 0);
    await expectLater(
        c.cloudSave.save('main', 1, expectedVersion: 0), throwsA(isA<SaveConflictException>()));
    final loaded = await c.cloudSave.load('main');
    expect(loaded!.data, {'gold': 5});
    expect(loaded.version, saved.version);
    expect(await c.cloudSave.delete('main'), isTrue);
    c.close();
  }, skip: skip);
}
