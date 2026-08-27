import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// App-wide presentation settings kept separate from the editing session.
///
/// Locale and theme changes intentionally do not touch [EditorController] or
/// any edit/history state. Values are applied in memory immediately, then
/// serialized in order so rapid toggles cannot leave an older value on disk.
class AppSettingsController extends ChangeNotifier {
  AppSettingsController({
    SharedPreferences? preferences,
    required Locale locale,
    ThemeMode themeMode = ThemeMode.dark,
  }) : _preferences = preferences,
       _locale = normalizeLocale(locale),
       _themeMode = _normalizeThemeMode(themeMode);

  static const Locale traditionalChineseLocale = Locale('zh', 'TW');
  static const Locale englishLocale = Locale('en');
  static const List<Locale> supportedLocales = <Locale>[
    traditionalChineseLocale,
    englishLocale,
  ];

  static const String _localePreferenceKey = 'app_settings.locale';
  static const String _themePreferenceKey = 'app_settings.theme';
  static const String _traditionalChineseTag = 'zh_TW';
  static const String _englishTag = 'en';

  final SharedPreferences? _preferences;
  Locale _locale;
  ThemeMode _themeMode;
  Future<void> _pendingPersistence = Future<void>.value();

  Locale get locale => _locale;
  ThemeMode get themeMode => _themeMode;
  bool get isEnglish => _locale.languageCode == 'en';
  bool get isDarkMode => _themeMode == ThemeMode.dark;

  /// Loads saved choices, or resolves the first-run locale from the platform.
  ///
  /// English platform locales map to [englishLocale]. Traditional Chinese
  /// platform locales map to [traditionalChineseLocale]. All unsupported
  /// locales safely fall back to Traditional Chinese. Dark is always the
  /// first-run theme, regardless of the system brightness.
  static Future<AppSettingsController> load({
    SharedPreferences? preferences,
    Iterable<Locale>? systemLocales,
    @visibleForTesting Future<SharedPreferences> Function()? preferencesLoader,
  }) async {
    SharedPreferences? resolvedPreferences = preferences;
    String? storedLocale;
    String? storedTheme;
    try {
      resolvedPreferences ??=
          await (preferencesLoader?.call() ?? SharedPreferences.getInstance());
      storedLocale = resolvedPreferences.getString(_localePreferenceKey);
      storedTheme = resolvedPreferences.getString(_themePreferenceKey);
    } catch (_) {
      // Settings must never block app startup. When platform persistence is
      // unavailable, keep the selected locale and theme in memory for this run.
      resolvedPreferences = null;
      storedLocale = null;
      storedTheme = null;
    }
    final firstRunLocale = resolveFirstRunLocale(
      systemLocales ?? ui.PlatformDispatcher.instance.locales,
    );

    return AppSettingsController(
      preferences: resolvedPreferences,
      locale: _localeFromStoredValue(storedLocale) ?? firstRunLocale,
      themeMode: _themeModeFromStoredValue(storedTheme),
    );
  }

  static Locale resolveFirstRunLocale(Iterable<Locale> systemLocales) {
    for (final locale in systemLocales) {
      if (locale.languageCode.toLowerCase() == 'en') {
        return englishLocale;
      }
      final isTraditionalChinese =
          locale.languageCode.toLowerCase() == 'zh' &&
          (locale.countryCode?.toUpperCase() == 'TW' ||
              locale.scriptCode?.toLowerCase() == 'hant');
      if (isTraditionalChinese) {
        return traditionalChineseLocale;
      }
    }
    return traditionalChineseLocale;
  }

  static Locale normalizeLocale(Locale locale) {
    return locale.languageCode.toLowerCase() == 'en'
        ? englishLocale
        : traditionalChineseLocale;
  }

  Future<void> setLocale(Locale value) {
    final normalized = normalizeLocale(value);
    if (_locale == normalized) {
      return _pendingPersistence;
    }
    _locale = normalized;
    notifyListeners();
    return _queueStringWrite(
      _localePreferenceKey,
      isEnglish ? _englishTag : _traditionalChineseTag,
    );
  }

  Future<void> toggleLocale() {
    return setLocale(isEnglish ? traditionalChineseLocale : englishLocale);
  }

  Future<void> setThemeMode(ThemeMode value) {
    final normalized = _normalizeThemeMode(value);
    if (_themeMode == normalized) {
      return _pendingPersistence;
    }
    _themeMode = normalized;
    notifyListeners();
    return _queueStringWrite(
      _themePreferenceKey,
      isDarkMode ? 'dark' : 'light',
    );
  }

  Future<void> toggleThemeMode() {
    return setThemeMode(isDarkMode ? ThemeMode.light : ThemeMode.dark);
  }

  Future<void> _queueStringWrite(String key, String value) {
    final preferences = _preferences;
    if (preferences == null) {
      return _pendingPersistence;
    }
    final write = _pendingPersistence.then<void>((_) async {
      await preferences.setString(key, value);
    });
    _pendingPersistence = write.catchError((Object _) {
      // Keep the queue usable after a platform persistence failure. The
      // original [write] future still reports the error to an awaiting caller.
    });
    return write;
  }

  static Locale? _localeFromStoredValue(String? value) {
    return switch (value) {
      _englishTag => englishLocale,
      _traditionalChineseTag => traditionalChineseLocale,
      _ => null,
    };
  }

  static ThemeMode _themeModeFromStoredValue(String? value) {
    return value == 'light' ? ThemeMode.light : ThemeMode.dark;
  }

  static ThemeMode _normalizeThemeMode(ThemeMode value) {
    return value == ThemeMode.light ? ThemeMode.light : ThemeMode.dark;
  }
}

class AppSettingsScope extends InheritedNotifier<AppSettingsController> {
  const AppSettingsScope({
    super.key,
    required AppSettingsController controller,
    required super.child,
  }) : super(notifier: controller);

  static AppSettingsController of(BuildContext context) {
    final scope = context
        .dependOnInheritedWidgetOfExactType<AppSettingsScope>();
    assert(scope != null, 'No AppSettingsScope found in this context.');
    return scope!.notifier!;
  }

  static AppSettingsController? maybeOf(BuildContext context) {
    return context
        .dependOnInheritedWidgetOfExactType<AppSettingsScope>()
        ?.notifier;
  }
}
