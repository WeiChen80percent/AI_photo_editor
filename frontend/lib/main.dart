import 'package:flutter/material.dart';

import 'app_theme.dart';
import 'editor_screen.dart';

void main() {
  runApp(const MyApp());
}

class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'AI 修圖',
      theme: buildAppTheme(),
      home: const EditorScreen(),
    );
  }
}
