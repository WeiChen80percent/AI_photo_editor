import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// Legacy dark palette kept while existing widgets migrate to
/// [BuildContext.editorColors].
///
/// New or migrated UI should use [Theme.of] for Material colors and
/// [BuildContext.editorColors] for editor-specific semantic colors.
abstract final class AppColors {
  static const background = Color(0xFF101216);
  static const surface = Color(0xFF171A20);
  static const surfaceRaised = Color(0xFF1D2027);
  static const surfaceSoft = Color(0xFF242832);
  static const border = Color(0xFF30343D);
  static const borderStrong = Color(0xFF414652);
  static const textPrimary = Color(0xFFF7F7FA);
  static const textSecondary = Color(0xFFA7ABB5);
  static const textMuted = Color(0xFF737985);
  static const accent = Color(0xFF6C75F5);
  static const accentBright = Color(0xFF8990FF);
  static const accentSoft = Color(0x286C75F5);
  static const success = Color(0xFF62C995);
  static const warning = Color(0xFFFFBE64);
  static const error = Color(0xFFFF7B7B);
  static const imageStage = Color(0xFF090A0D);
}

abstract final class AppSpacing {
  static const double xxs = 4;
  static const double xs = 8;
  static const double sm = 12;
  static const double md = 16;
  static const double lg = 24;
  static const double xl = 32;
}

abstract final class AppRadii {
  static const double small = 10;
  static const double medium = 14;
  static const double large = 18;
  static const double sheet = 24;
}

/// Editor-specific semantic colors that are not fully represented by
/// Material's [ColorScheme].
///
/// [imageStage], [onImageStage], and [imageStageMuted] intentionally remain
/// the same in light and dark themes so the UI brightness does not bias photo
/// exposure and color judgement.
@immutable
class EditorThemeColors extends ThemeExtension<EditorThemeColors> {
  const EditorThemeColors({
    required this.background,
    required this.surface,
    required this.surfaceRaised,
    required this.surfaceSoft,
    required this.border,
    required this.borderStrong,
    required this.textPrimary,
    required this.textSecondary,
    required this.textMuted,
    required this.accent,
    required this.accentBright,
    required this.accentSoft,
    required this.success,
    required this.warning,
    required this.error,
    required this.imageStage,
    required this.onImageStage,
    required this.imageStageMuted,
    required this.modalBarrier,
  });

  static const EditorThemeColors dark = EditorThemeColors(
    background: Color(0xFF101216),
    surface: Color(0xFF171A20),
    surfaceRaised: Color(0xFF1D2027),
    surfaceSoft: Color(0xFF242832),
    border: Color(0xFF30343D),
    borderStrong: Color(0xFF414652),
    textPrimary: Color(0xFFF7F7FA),
    textSecondary: Color(0xFFA7ABB5),
    textMuted: Color(0xFF737985),
    accent: Color(0xFF6C75F5),
    accentBright: Color(0xFF8990FF),
    accentSoft: Color(0x286C75F5),
    success: Color(0xFF62C995),
    warning: Color(0xFFFFBE64),
    error: Color(0xFFFF7B7B),
    imageStage: Color(0xFF090A0D),
    onImageStage: Color(0xFFF7F7FA),
    imageStageMuted: Color(0xFFA7ABB5),
    modalBarrier: Color(0xB3000000),
  );

  static const EditorThemeColors light = EditorThemeColors(
    background: Color(0xFFF4F5F8),
    surface: Color(0xFFFFFFFF),
    surfaceRaised: Color(0xFFFFFFFF),
    surfaceSoft: Color(0xFFECEEF4),
    border: Color(0xFFD8DBE4),
    borderStrong: Color(0xFFB8BECA),
    textPrimary: Color(0xFF1A1D25),
    textSecondary: Color(0xFF545B68),
    textMuted: Color(0xFF737B89),
    accent: Color(0xFF515BDE),
    accentBright: Color(0xFF6871EB),
    accentSoft: Color(0x24515BDE),
    success: Color(0xFF18794E),
    warning: Color(0xFF9C5B00),
    error: Color(0xFFBA1A1A),
    imageStage: Color(0xFF090A0D),
    onImageStage: Color(0xFFF7F7FA),
    imageStageMuted: Color(0xFFA7ABB5),
    modalBarrier: Color(0x73000000),
  );

  final Color background;
  final Color surface;
  final Color surfaceRaised;
  final Color surfaceSoft;
  final Color border;
  final Color borderStrong;
  final Color textPrimary;
  final Color textSecondary;
  final Color textMuted;
  final Color accent;
  final Color accentBright;
  final Color accentSoft;
  final Color success;
  final Color warning;
  final Color error;
  final Color imageStage;
  final Color onImageStage;
  final Color imageStageMuted;
  final Color modalBarrier;

  @override
  EditorThemeColors copyWith({
    Color? background,
    Color? surface,
    Color? surfaceRaised,
    Color? surfaceSoft,
    Color? border,
    Color? borderStrong,
    Color? textPrimary,
    Color? textSecondary,
    Color? textMuted,
    Color? accent,
    Color? accentBright,
    Color? accentSoft,
    Color? success,
    Color? warning,
    Color? error,
    Color? imageStage,
    Color? onImageStage,
    Color? imageStageMuted,
    Color? modalBarrier,
  }) {
    return EditorThemeColors(
      background: background ?? this.background,
      surface: surface ?? this.surface,
      surfaceRaised: surfaceRaised ?? this.surfaceRaised,
      surfaceSoft: surfaceSoft ?? this.surfaceSoft,
      border: border ?? this.border,
      borderStrong: borderStrong ?? this.borderStrong,
      textPrimary: textPrimary ?? this.textPrimary,
      textSecondary: textSecondary ?? this.textSecondary,
      textMuted: textMuted ?? this.textMuted,
      accent: accent ?? this.accent,
      accentBright: accentBright ?? this.accentBright,
      accentSoft: accentSoft ?? this.accentSoft,
      success: success ?? this.success,
      warning: warning ?? this.warning,
      error: error ?? this.error,
      imageStage: imageStage ?? this.imageStage,
      onImageStage: onImageStage ?? this.onImageStage,
      imageStageMuted: imageStageMuted ?? this.imageStageMuted,
      modalBarrier: modalBarrier ?? this.modalBarrier,
    );
  }

  @override
  EditorThemeColors lerp(
    covariant ThemeExtension<EditorThemeColors>? other,
    double t,
  ) {
    if (other is! EditorThemeColors) {
      return this;
    }
    return EditorThemeColors(
      background: Color.lerp(background, other.background, t)!,
      surface: Color.lerp(surface, other.surface, t)!,
      surfaceRaised: Color.lerp(surfaceRaised, other.surfaceRaised, t)!,
      surfaceSoft: Color.lerp(surfaceSoft, other.surfaceSoft, t)!,
      border: Color.lerp(border, other.border, t)!,
      borderStrong: Color.lerp(borderStrong, other.borderStrong, t)!,
      textPrimary: Color.lerp(textPrimary, other.textPrimary, t)!,
      textSecondary: Color.lerp(textSecondary, other.textSecondary, t)!,
      textMuted: Color.lerp(textMuted, other.textMuted, t)!,
      accent: Color.lerp(accent, other.accent, t)!,
      accentBright: Color.lerp(accentBright, other.accentBright, t)!,
      accentSoft: Color.lerp(accentSoft, other.accentSoft, t)!,
      success: Color.lerp(success, other.success, t)!,
      warning: Color.lerp(warning, other.warning, t)!,
      error: Color.lerp(error, other.error, t)!,
      imageStage: Color.lerp(imageStage, other.imageStage, t)!,
      onImageStage: Color.lerp(onImageStage, other.onImageStage, t)!,
      imageStageMuted: Color.lerp(imageStageMuted, other.imageStageMuted, t)!,
      modalBarrier: Color.lerp(modalBarrier, other.modalBarrier, t)!,
    );
  }
}

extension EditorThemeBuildContext on BuildContext {
  EditorThemeColors get editorColors =>
      Theme.of(this).extension<EditorThemeColors>() ?? EditorThemeColors.dark;
}

/// Backwards-compatible entry point used by existing widget tests.
ThemeData buildAppTheme() => buildDarkAppTheme();

ThemeData buildDarkAppTheme() =>
    _buildAppTheme(Brightness.dark, EditorThemeColors.dark);

ThemeData buildLightAppTheme() =>
    _buildAppTheme(Brightness.light, EditorThemeColors.light);

ThemeData _buildAppTheme(Brightness brightness, EditorThemeColors colors) {
  final colorScheme =
      ColorScheme.fromSeed(
        seedColor: colors.accent,
        brightness: brightness,
        surface: colors.surface,
      ).copyWith(
        primary: colors.accent,
        onPrimary: Colors.white,
        secondary: colors.accentBright,
        surface: colors.surface,
        surfaceContainerLow: colors.background,
        surfaceContainer: colors.surface,
        surfaceContainerHigh: colors.surfaceRaised,
        surfaceContainerHighest: colors.surfaceSoft,
        onSurface: colors.textPrimary,
        onSurfaceVariant: colors.textSecondary,
        error: colors.error,
        onError: Colors.white,
        outline: colors.border,
        outlineVariant: colors.borderStrong,
        scrim: colors.modalBarrier,
      );

  final textTheme = TextTheme(
    headlineSmall: TextStyle(
      fontSize: 22,
      height: 1.25,
      fontWeight: FontWeight.w700,
      letterSpacing: -0.2,
      color: colors.textPrimary,
    ),
    titleLarge: TextStyle(
      fontSize: 19,
      height: 1.3,
      fontWeight: FontWeight.w700,
      color: colors.textPrimary,
    ),
    titleMedium: TextStyle(
      fontSize: 16,
      height: 1.35,
      fontWeight: FontWeight.w600,
      color: colors.textPrimary,
    ),
    bodyLarge: TextStyle(fontSize: 15, height: 1.5, color: colors.textPrimary),
    bodyMedium: TextStyle(
      fontSize: 14,
      height: 1.45,
      color: colors.textPrimary,
    ),
    bodySmall: TextStyle(
      fontSize: 12,
      height: 1.4,
      color: colors.textSecondary,
    ),
    labelLarge: TextStyle(
      fontSize: 14,
      height: 1.2,
      fontWeight: FontWeight.w600,
      color: colors.textPrimary,
    ),
    labelMedium: TextStyle(
      fontSize: 12,
      height: 1.2,
      fontWeight: FontWeight.w600,
      color: colors.textSecondary,
    ),
  );

  return ThemeData(
    useMaterial3: true,
    brightness: brightness,
    colorScheme: colorScheme,
    extensions: <ThemeExtension<dynamic>>[colors],
    scaffoldBackgroundColor: colors.background,
    canvasColor: colors.background,
    textTheme: textTheme,
    splashFactory: InkSparkle.splashFactory,
    dividerColor: colors.border,
    disabledColor: colors.textMuted.withValues(alpha: 0.46),
    focusColor: colors.accentSoft,
    hoverColor: colors.accentSoft.withValues(alpha: 0.72),
    highlightColor: colors.accentSoft,
    appBarTheme: AppBarTheme(
      backgroundColor: colors.background,
      foregroundColor: colors.textPrimary,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      centerTitle: true,
      systemOverlayStyle: brightness == Brightness.dark
          ? SystemUiOverlayStyle.light
          : SystemUiOverlayStyle.dark,
      titleTextStyle: TextStyle(
        fontSize: 19,
        fontWeight: FontWeight.w700,
        color: colors.textPrimary,
      ),
    ),
    bottomSheetTheme: BottomSheetThemeData(
      backgroundColor: colors.surface,
      modalBackgroundColor: colors.surface,
      surfaceTintColor: Colors.transparent,
      modalBarrierColor: colors.modalBarrier,
      showDragHandle: false,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(
          top: Radius.circular(AppRadii.sheet),
        ),
      ),
    ),
    dialogTheme: DialogThemeData(
      backgroundColor: colors.surface,
      surfaceTintColor: Colors.transparent,
      titleTextStyle: textTheme.titleLarge,
      contentTextStyle: textTheme.bodyMedium,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppRadii.large),
        side: BorderSide(color: colors.border),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: colors.surfaceSoft,
      hintStyle: TextStyle(color: colors.textMuted),
      contentPadding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.md,
        vertical: 14,
      ),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AppRadii.medium),
        borderSide: BorderSide(color: colors.border),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AppRadii.medium),
        borderSide: BorderSide(color: colors.border),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AppRadii.medium),
        borderSide: BorderSide(color: colors.accent, width: 1.4),
      ),
      errorBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AppRadii.medium),
        borderSide: BorderSide(color: colors.error),
      ),
      focusedErrorBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AppRadii.medium),
        borderSide: BorderSide(color: colors.error, width: 1.4),
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        minimumSize: const Size(48, 48),
        disabledBackgroundColor: colors.surfaceSoft,
        disabledForegroundColor: colors.textMuted,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppRadii.small),
        ),
        textStyle: textTheme.labelLarge,
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        minimumSize: const Size(48, 48),
        foregroundColor: colors.textPrimary,
        disabledForegroundColor: colors.textMuted,
        side: BorderSide(color: colors.borderStrong),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppRadii.small),
        ),
        textStyle: textTheme.labelLarge,
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(
        minimumSize: const Size(44, 44),
        foregroundColor: colors.accent,
        disabledForegroundColor: colors.textMuted,
        textStyle: textTheme.labelLarge,
      ),
    ),
    iconButtonTheme: IconButtonThemeData(
      style: IconButton.styleFrom(
        foregroundColor: colors.textSecondary,
        disabledForegroundColor: colors.textMuted,
        focusColor: colors.accentSoft,
        minimumSize: const Size(48, 48),
      ),
    ),
    sliderTheme: SliderThemeData(
      activeTrackColor: colors.accent,
      inactiveTrackColor: colors.borderStrong,
      disabledActiveTrackColor: colors.textMuted.withValues(alpha: 0.44),
      disabledInactiveTrackColor: colors.border,
      thumbColor: colors.textPrimary,
      disabledThumbColor: colors.textMuted,
      overlayColor: colors.accentSoft,
      valueIndicatorColor: colors.surfaceRaised,
      valueIndicatorTextStyle: textTheme.labelMedium,
      trackHeight: 3,
    ),
    scrollbarTheme: ScrollbarThemeData(
      thumbColor: WidgetStateProperty.resolveWith<Color?>((states) {
        if (states.contains(WidgetState.dragged)) {
          return colors.textSecondary;
        }
        if (states.contains(WidgetState.hovered)) {
          return colors.textMuted;
        }
        return colors.borderStrong;
      }),
      trackColor: WidgetStatePropertyAll<Color>(colors.surfaceSoft),
      radius: const Radius.circular(AppRadii.small),
      thickness: const WidgetStatePropertyAll<double>(6),
    ),
    snackBarTheme: SnackBarThemeData(
      backgroundColor: colors.surfaceRaised,
      contentTextStyle: textTheme.bodyMedium,
      actionTextColor: colors.accentBright,
      behavior: SnackBarBehavior.floating,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppRadii.small),
        side: BorderSide(color: colors.border),
      ),
    ),
    tooltipTheme: TooltipThemeData(
      decoration: BoxDecoration(
        color: colors.surfaceRaised,
        borderRadius: BorderRadius.circular(AppRadii.small),
        border: Border.all(color: colors.border),
      ),
      textStyle: TextStyle(color: colors.textPrimary),
    ),
    progressIndicatorTheme: ProgressIndicatorThemeData(
      color: colors.accent,
      linearTrackColor: colors.border,
      circularTrackColor: colors.border,
    ),
    switchTheme: SwitchThemeData(
      thumbColor: WidgetStateProperty.resolveWith<Color?>((states) {
        if (states.contains(WidgetState.disabled)) {
          return colors.textMuted;
        }
        return states.contains(WidgetState.selected)
            ? Colors.white
            : colors.textSecondary;
      }),
      trackColor: WidgetStateProperty.resolveWith<Color?>((states) {
        if (states.contains(WidgetState.disabled)) {
          return colors.border;
        }
        return states.contains(WidgetState.selected)
            ? colors.accent
            : colors.surfaceSoft;
      }),
    ),
  );
}
