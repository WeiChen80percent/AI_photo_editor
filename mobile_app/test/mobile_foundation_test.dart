import 'dart:convert';
import 'dart:typed_data';

import 'package:ai_photo_editor_mobile/api_service.dart';
import 'package:ai_photo_editor_mobile/mobile_image_io.dart';
import 'package:ai_photo_editor_mobile/mobile_workspace.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('server URL validation', () {
    test('accepts HTTP and HTTPS and removes trailing slashes', () {
      expect(
        MobileWorkspaceStore.normalizeServerUrl(
          '  http://192.168.1.20:8000///  ',
        ),
        'http://192.168.1.20:8000',
      );
      expect(
        MobileWorkspaceStore.normalizeServerUrl(
          'https://editor.example.test/api/',
        ),
        'https://editor.example.test/api',
      );
    });

    test('rejects unsafe or unusable server URLs', () {
      for (final value in <String>[
        '',
        '192.168.1.20:8000',
        'ftp://192.168.1.20',
        'http://user:password@example.test',
        'http://example.test?token=secret',
        'http://example.test/#fragment',
      ]) {
        expect(
          () => MobileWorkspaceStore.normalizeServerUrl(value),
          throwsFormatException,
          reason: value,
        );
      }
    });
  });

  group('workspace persistence', () {
    setUp(() => SharedPreferences.setMockInitialValues(<String, Object>{}));

    test(
      'keeps server, session selection and interrupted picker role',
      () async {
        final preferences = await SharedPreferences.getInstance();
        final store = MobileWorkspaceStore(preferences);

        await store.setServer('http://192.168.1.20:8000/');
        await store.rememberSession(
          'http://192.168.1.20:8000',
          'session-1',
          'edit-2',
        );
        await store.beginPicker('http://192.168.1.20:8000', 'reference');
        await store.flush();

        expect(store.baseUrl, 'http://192.168.1.20:8000');
        expect(store.sessionFor('http://another-server:8000'), isNull);
        expect(store.sessionFor('http://192.168.1.20:8000'), <String, dynamic>{
          'server': 'http://192.168.1.20:8000',
          'session_id': 'session-1',
          'selected_edit_id': 'edit-2',
        });
        expect(store.pendingPicker, <String, dynamic>{
          'server': 'http://192.168.1.20:8000',
          'role': 'reference',
        });

        await store.endPicker();
        await store.forgetSession();
        expect(store.pendingPicker, isNull);
        expect(store.sessionFor('http://192.168.1.20:8000'), isNull);
      },
    );
  });

  group('backend health contract', () {
    test('accepts only the expected photo editor health response', () async {
      final client = MockClient((request) async {
        expect(request.method, 'GET');
        expect(request.url, Uri.parse('http://server.test:8000/health'));
        return http.Response(
          jsonEncode(<String, String>{'status': 'good'}),
          200,
          headers: <String, String>{'content-type': 'application/json'},
        );
      });
      final api = ApiService(
        baseUrl: 'http://server.test:8000/',
        client: client,
      );

      await expectLater(api.checkConnection(), completes);
    });

    test('rejects a different service on the configured address', () async {
      final client = MockClient(
        (_) async =>
            http.Response(jsonEncode(<String, String>{'status': 'ok'}), 200),
      );
      final api = ApiService(
        baseUrl: 'http://server.test:8000',
        client: client,
      );

      await expectLater(
        api.checkConnection(),
        throwsA(
          isA<ApiException>().having(
            (error) => error.code,
            'code',
            'invalid_health',
          ),
        ),
      );
    });

    test('preserves structured backend errors', () async {
      final client = MockClient(
        (_) async => http.Response(
          jsonEncode(<String, Object>{
            'detail': <String, String>{
              'code': 'backend_unavailable',
              'message': 'Try later',
            },
          }),
          503,
        ),
      );
      final api = ApiService(
        baseUrl: 'http://server.test:8000',
        client: client,
      );

      await expectLater(
        api.checkConnection(),
        throwsA(
          isA<ApiException>()
              .having((error) => error.statusCode, 'statusCode', 503)
              .having((error) => error.code, 'code', 'backend_unavailable')
              .having((error) => error.message, 'message', 'Try later'),
        ),
      );
    });

    test('builds relative result URLs without changing absolute URLs', () {
      final api = ApiService(
        baseUrl: 'http://server.test:8000/',
        client: MockClient((_) async => http.Response('{}', 200)),
      );

      expect(
        api.buildImageUrl('/storage/results/result.png'),
        'http://server.test:8000/storage/results/result.png',
      );
      expect(
        api.buildImageUrl('https://cdn.example.test/result.png'),
        'https://cdn.example.test/result.png',
      );
    });
  });

  group('mobile image validation', () {
    test('decodes a real PNG and returns its original dimensions', () async {
      final bytes = base64Decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
      );

      final image = await MobileImageIO.inspect(bytes);

      expect(image.width, 1);
      expect(image.height, 1);
      expect(image.extension, 'png');
      expect(image.bytes, same(bytes));
    });

    test('rejects unsupported signatures and corrupt PNG data', () async {
      await expectLater(
        MobileImageIO.inspect(
          Uint8List.fromList(<int>[71, 73, 70, 56, 57, 97]),
        ),
        throwsA(
          isA<MobileImageException>().having(
            (error) => error.code,
            'code',
            'image_format',
          ),
        ),
      );
      await expectLater(
        MobileImageIO.inspect(
          Uint8List.fromList(<int>[137, 80, 78, 71, 13, 10, 26, 10]),
        ),
        throwsA(
          isA<MobileImageException>().having(
            (error) => error.code,
            'code',
            'image_invalid',
          ),
        ),
      );
    });

    test('rejects data beyond the 40 MiB limit before decoding', () async {
      await expectLater(
        MobileImageIO.inspect(Uint8List(MobileImageIO.maxBytes + 1)),
        throwsA(
          isA<MobileImageException>().having(
            (error) => error.code,
            'code',
            'image_too_large',
          ),
        ),
      );
    });

    test('rejects images beyond the 24 megapixel limit', () async {
      // Valid PNG metadata declaring 5000 x 5000 pixels. The descriptor reads
      // dimensions without decoding or allocating the full image.
      final bytes = base64Decode(
        'iVBORw0KGgoAAAANSUhEUgAAE4gAABOICAQAAAD3kU9AAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
      );

      await expectLater(
        MobileImageIO.inspect(bytes),
        throwsA(
          isA<MobileImageException>().having(
            (error) => error.code,
            'code',
            'image_dimensions',
          ),
        ),
      );
    });
  });
}
