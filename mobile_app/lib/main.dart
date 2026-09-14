import 'package:flutter/material.dart';

import 'app_settings.dart';
import 'app_theme.dart';
import 'editor_screen.dart';
import 'l10n/generated/app_localizations.dart';
import 'mobile_workspace.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final settingsController = await AppSettingsController.load();
  final workspace = await MobileWorkspaceStore.load();
  runApp(MyApp(settingsController: settingsController, workspace: workspace));
}

class MyApp extends StatelessWidget {
  const MyApp({
    super.key,
    required this.settingsController,
    this.home,
    this.workspace,
  });

  final AppSettingsController settingsController;
  final Widget? home;
  final MobileWorkspaceStore? workspace;

  @override
  Widget build(BuildContext context) {
    return AppSettingsScope(
      controller: settingsController,
      child: AnimatedBuilder(
        animation: settingsController,
        builder: (context, _) {
          return MaterialApp(
            debugShowCheckedModeBanner: false,
            onGenerateTitle: (context) => AppLocalizations.of(context).appTitle,
            locale: settingsController.locale,
            localizationsDelegates: AppLocalizations.localizationsDelegates,
            supportedLocales: AppSettingsController.supportedLocales,
            theme: buildLightAppTheme(),
            darkTheme: buildDarkAppTheme(),
            themeMode: settingsController.themeMode,
            home: home ?? EditorScreen(workspace: workspace),
          );
        },
      ),
    );
  }
}
