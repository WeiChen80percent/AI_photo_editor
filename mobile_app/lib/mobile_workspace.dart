import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import 'api_service.dart';

/// Navigation metadata only. Images and edit recipes remain on the backend.
/// Writes are serialized so a delayed write cannot resurrect a cleared session.
class MobileWorkspaceStore {
  MobileWorkspaceStore(this._preferences);

  final SharedPreferences? _preferences;
  Future<void> _writes = Future<void>.value();
  static const _serverKey = 'mobile.server.v1';
  static const _sessionKey = 'mobile.session.v1';
  static const _pickerKey = 'mobile.picker.v1';

  static Future<MobileWorkspaceStore> load() async {
    try {
      return MobileWorkspaceStore(await SharedPreferences.getInstance());
    } catch (_) {
      return MobileWorkspaceStore(null);
    }
  }

  bool get available => _preferences != null;
  String get baseUrl {
    final saved = _preferences?.getString(_serverKey);
    try {
      return normalizeServerUrl(saved ?? ApiService.environmentBaseUrl);
    } on FormatException {
      return normalizeServerUrl(ApiService.environmentBaseUrl);
    }
  }

  static String normalizeServerUrl(String input) {
    final uri = Uri.tryParse(input.trim());
    if (uri == null ||
        !const ['http', 'https'].contains(uri.scheme) ||
        uri.host.isEmpty ||
        uri.userInfo.isNotEmpty ||
        uri.hasQuery ||
        uri.hasFragment ||
        (uri.hasPort && (uri.port < 1 || uri.port > 65535))) {
      throw const FormatException(
        'Enter an HTTP(S) server URL without credentials, query or fragment.',
      );
    }
    return uri.toString().replaceFirst(RegExp(r'/+$'), '');
  }

  Map<String, dynamic>? _read(String key) {
    try {
      final text = _preferences?.getString(key);
      if (text == null) return null;
      return Map<String, dynamic>.from(jsonDecode(text) as Map);
    } catch (_) {
      return null;
    }
  }

  Map<String, dynamic>? sessionFor(String server) {
    final value = _read(_sessionKey);
    if (value?['server'] != server || value?['session_id'] is! String) {
      return null;
    }
    return value;
  }

  Map<String, dynamic>? get pendingPicker => _read(_pickerKey);

  Future<void> _write(Future<bool> Function(SharedPreferences) action) {
    final operation = _writes.then((_) async {
      final preferences = _preferences;
      if (preferences == null || !await action(preferences)) {
        throw StateError('Local workspace settings could not be saved.');
      }
    });
    _writes = operation.catchError((Object _) {});
    return operation;
  }

  Future<void> setServer(String server) => _write(
    (prefs) => prefs.setString(_serverKey, normalizeServerUrl(server)),
  );

  Future<void> rememberSession(String server, String session, String? edit) =>
      _write(
        (prefs) => prefs.setString(
          _sessionKey,
          jsonEncode({
            'server': server,
            'session_id': session,
            'selected_edit_id': edit,
          }),
        ),
      );

  Future<void> forgetSession() => _write((prefs) => prefs.remove(_sessionKey));

  Future<void> beginPicker(String server, String role) => _write(
    (prefs) => prefs.setString(
      _pickerKey,
      jsonEncode({'server': server, 'role': role}),
    ),
  );

  Future<void> endPicker() => _write((prefs) => prefs.remove(_pickerKey));

  Future<void> flush() => _writes;
}
