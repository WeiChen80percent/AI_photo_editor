import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

import 'api_service.dart';
import 'app_settings.dart';
import 'app_theme.dart';
import 'edit_models.dart';
import 'editor_canvas.dart';
import 'editor_controller.dart';
import 'editor_localizations.dart';
import 'editor_panels.dart';
import 'l10n/l10n_context.dart';
import 'tool_dock.dart';

class EditorScreen extends StatefulWidget {
  const EditorScreen({super.key, this.controller, this.imagePicker});

  final EditorController? controller;
  final ImagePicker? imagePicker;

  @override
  State<EditorScreen> createState() => _EditorScreenState();
}

class _EditorScreenState extends State<EditorScreen> {
  late final EditorController _controller;
  late final bool _ownsController;
  late final ImagePicker _imagePicker;
  late final TextEditingController _promptTextController;
  BuildContext? _toolSheetContext;
  bool _toolSheetOpen = false;
  bool _toolSheetDismissScheduled = false;
  bool _preserveActiveToolAfterSheetDismiss = false;
  bool _detailsInspectorOpen = false;

  @override
  void initState() {
    super.initState();
    _ownsController = widget.controller == null;
    _controller = widget.controller ?? EditorController(api: ApiService());
    _imagePicker = widget.imagePicker ?? ImagePicker();
    _promptTextController = TextEditingController.fromValue(
      TextEditingValue(
        text: _controller.promptDraft,
        selection: TextSelection.collapsed(
          offset: _controller.promptDraft.length,
        ),
      ),
    );
    _promptTextController.addListener(_syncPromptDraftFromTextController);
    _controller.addListener(_syncPromptTextFromEditorController);
  }

  @override
  void dispose() {
    _controller.removeListener(_syncPromptTextFromEditorController);
    _promptTextController.removeListener(_syncPromptDraftFromTextController);
    _promptTextController.dispose();
    if (_ownsController) {
      _controller.dispose();
    }
    super.dispose();
  }

  void _syncPromptDraftFromTextController() {
    final text = _promptTextController.text;
    if (_controller.promptDraft != text) {
      _controller.setPromptDraft(text);
    }
  }

  void _syncPromptTextFromEditorController() {
    final draft = _controller.promptDraft;
    if (_promptTextController.text == draft) {
      return;
    }
    _promptTextController.value = TextEditingValue(
      text: draft,
      selection: TextSelection.collapsed(offset: draft.length),
    );
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, _) {
        final l10n = context.l10n;
        final colors = context.editorColors;
        final settings = AppSettingsScope.maybeOf(context);
        final isEnglish =
            settings?.isEnglish ??
            Localizations.localeOf(context).languageCode == 'en';
        final isDark =
            settings?.isDarkMode ??
            Theme.of(context).brightness == Brightness.dark;
        final isNarrowAppBar = MediaQuery.sizeOf(context).width < 480;
        final actionConstraints = BoxConstraints.tightFor(
          width: isNarrowAppBar ? 44 : 48,
          height: 48,
        );
        return Scaffold(
          appBar: AppBar(
            toolbarHeight: 58,
            titleSpacing: isNarrowAppBar ? AppSpacing.xxs : 0,
            title: Text(
              isNarrowAppBar ? l10n.appCompactTitle : l10n.appTitle,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
            leadingWidth: isNarrowAppBar ? 44 : null,
            leading: _controller.sessionId == null && isNarrowAppBar
                ? null
                : _controller.sessionId == null
                ? Padding(
                    padding: const EdgeInsets.all(14),
                    child: Icon(
                      Icons.auto_fix_high,
                      color: colors.accentBright,
                    ),
                  )
                : IconButton(
                    tooltip: l10n.clearCurrentWork,
                    onPressed: _confirmClearWorkspace,
                    icon: const Icon(Icons.close),
                  ),
            actions: [
              IconButton(
                key: const Key('language_toggle'),
                constraints: actionConstraints,
                tooltip: isEnglish
                    ? l10n.switchToTraditionalChinese
                    : l10n.switchToEnglish,
                onPressed: settings == null
                    ? null
                    : () => unawaited(settings.toggleLocale()),
                icon: Text(
                  isEnglish ? '中' : 'EN',
                  maxLines: 1,
                  style: const TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.w800,
                  ),
                ),
              ),
              IconButton(
                key: const Key('theme_toggle'),
                constraints: actionConstraints,
                tooltip: isDark
                    ? l10n.switchToLightTheme
                    : l10n.switchToDarkTheme,
                onPressed: settings == null
                    ? null
                    : () => unawaited(settings.toggleThemeMode()),
                icon: Icon(
                  isDark ? Icons.light_mode_outlined : Icons.dark_mode_outlined,
                ),
              ),
              IconButton(
                key: const Key('appbar_pick_original'),
                constraints: actionConstraints,
                tooltip: _controller.hasOriginal
                    ? l10n.changeOriginal
                    : l10n.chooseOriginal,
                onPressed: _pickOriginal,
                icon: const Icon(Icons.add_photo_alternate_outlined),
              ),
              if (!isNarrowAppBar) const SizedBox(width: 2),
            ],
          ),
          body: LayoutBuilder(
            builder: (context, constraints) {
              final width = constraints.maxWidth;
              final isCompact = width < 600;
              final isExpanded = width >= 1024;
              final isLandscape =
                  MediaQuery.orientationOf(context) == Orientation.landscape;
              final shortLandscape = isLandscape && constraints.maxHeight < 360;
              final sideBySide =
                  isExpanded ||
                  (isLandscape && width >= (shortLandscape ? 480 : 760));
              final showDesktopInspector =
                  isExpanded &&
                  !_toolSheetOpen &&
                  (_controller.activeTool != null || _detailsInspectorOpen);
              if (isExpanded) {
                _dismissToolSheetForExpandedLayout();
              }
              return Column(
                children: [
                  if (_controller.statusMessage != null)
                    PanelMessage(
                      message: localizedPresentationMessage(
                        l10n,
                        _controller.statusPresentation,
                        legacyFallback: _controller.statusMessage,
                      ),
                      isError: false,
                    ),
                  Expanded(
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Expanded(
                          child: Padding(
                            padding: EdgeInsets.fromLTRB(
                              isCompact ? AppSpacing.sm : AppSpacing.md,
                              isCompact ? AppSpacing.sm : AppSpacing.md,
                              showDesktopInspector
                                  ? AppSpacing.sm
                                  : isCompact
                                  ? AppSpacing.sm
                                  : AppSpacing.md,
                              isCompact ? AppSpacing.sm : AppSpacing.md,
                            ),
                            child: EditorCanvas(
                              controller: _controller,
                              sideBySide: sideBySide,
                              onPickOriginal: _pickOriginal,
                              onOpenDetails: () => _openDetails(isExpanded),
                            ),
                          ),
                        ),
                        if (showDesktopInspector)
                          _DesktopInspector(
                            child: _detailsInspectorOpen
                                ? EditDetailsPanel(
                                    controller: _controller,
                                    onClose: () => setState(
                                      () => _detailsInspectorOpen = false,
                                    ),
                                  )
                                : _panelForTool(
                                    _controller.activeTool!,
                                    isExpanded: true,
                                  ),
                          ),
                      ],
                    ),
                  ),
                  EditorToolDock(
                    selectedTool: _controller.activeTool,
                    historyCount: _controller.history.length,
                    onSelected: (tool) =>
                        _handleToolSelected(tool, isExpanded: isExpanded),
                  ),
                ],
              );
            },
          ),
        );
      },
    );
  }

  Future<void> _handleToolSelected(
    EditorTool tool, {
    required bool isExpanded,
  }) async {
    if (tool != EditorTool.history && _controller.hasPhotoGitDraft) {
      final discard = await showDialog<bool>(
        context: context,
        builder: (dialogContext) {
          final l10n = dialogContext.l10n;
          return AlertDialog(
            title: Text(l10n.discardDraftTitle),
            content: Text(l10n.discardPhotoGitForTool),
            actions: [
              TextButton(
                onPressed: () => Navigator.of(dialogContext).pop(false),
                child: Text(l10n.actionBack),
              ),
              FilledButton(
                onPressed: () => Navigator.of(dialogContext).pop(true),
                child: Text(l10n.actionDiscardAndSwitch),
              ),
            ],
          );
        },
      );
      if (discard != true || !mounted) {
        return;
      }
      _controller.discardPhotoGitDraft();
    }

    if (isExpanded && _controller.activeTool == tool) {
      _controller.setActiveTool(null);
      return;
    }

    if (_detailsInspectorOpen) {
      setState(() => _detailsInspectorOpen = false);
    }

    _controller.setActiveTool(tool);
    if (tool == EditorTool.manual) {
      await _controller.openManual();
    } else if (tool == EditorTool.styles) {
      await _controller.openStyles();
    }
    if (!mounted || isExpanded) {
      return;
    }

    _toolSheetOpen = true;
    var preserveActiveTool = false;
    try {
      await showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        useSafeArea: true,
        backgroundColor: context.editorColors.surface,
        barrierColor: context.editorColors.modalBarrier,
        builder: (sheetContext) {
          _toolSheetContext = sheetContext;
          final initial =
              tool == EditorTool.manual ||
                  tool == EditorTool.history ||
                  tool == EditorTool.styles
              ? 0.72
              : 0.56;
          return AnimatedPadding(
            duration: const Duration(milliseconds: 120),
            padding: EdgeInsets.only(
              bottom: MediaQuery.viewInsetsOf(sheetContext).bottom,
            ),
            child: DraggableScrollableSheet(
              expand: false,
              initialChildSize: initial,
              minChildSize: 0.34,
              maxChildSize: 0.94,
              snap: true,
              snapSizes: const [0.5, 0.72, 0.94],
              builder: (context, scrollController) {
                return AnimatedBuilder(
                  animation: _controller,
                  builder: (_, _) {
                    return Column(
                      children: [
                        const SizedBox(height: 8),
                        Container(
                          width: 38,
                          height: 4,
                          decoration: BoxDecoration(
                            color: context.editorColors.borderStrong,
                            borderRadius: BorderRadius.circular(999),
                          ),
                        ),
                        const SizedBox(height: 4),
                        Expanded(
                          child: _panelForTool(
                            tool,
                            isExpanded: false,
                            sheetContext: sheetContext,
                            scrollController: scrollController,
                          ),
                        ),
                      ],
                    );
                  },
                );
              },
            ),
          );
        },
      );
    } finally {
      preserveActiveTool = _preserveActiveToolAfterSheetDismiss;
      _toolSheetOpen = false;
      _toolSheetContext = null;
      _toolSheetDismissScheduled = false;
      _preserveActiveToolAfterSheetDismiss = false;
    }
    if (mounted && !preserveActiveTool && _controller.activeTool == tool) {
      _controller.setActiveTool(null);
    } else if (mounted) {
      setState(() {});
    }
  }

  void _dismissToolSheetForExpandedLayout() {
    if (!_toolSheetOpen || _toolSheetDismissScheduled) {
      return;
    }
    _toolSheetDismissScheduled = true;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _toolSheetDismissScheduled = false;
      final sheetContext = _toolSheetContext;
      if (!mounted || !_toolSheetOpen || sheetContext == null) {
        return;
      }
      final navigator = Navigator.of(sheetContext);
      if (!navigator.canPop()) {
        return;
      }
      _preserveActiveToolAfterSheetDismiss = true;
      navigator.pop();
    });
  }

  Widget _panelForTool(
    EditorTool tool, {
    required bool isExpanded,
    BuildContext? sheetContext,
    ScrollController? scrollController,
  }) {
    void close() {
      if (isExpanded) {
        _controller.setActiveTool(null);
      } else if (sheetContext != null && Navigator.of(sheetContext).canPop()) {
        Navigator.of(sheetContext).pop();
      }
    }

    switch (tool) {
      case EditorTool.prompt:
        return PromptPanel(
          controller: _controller,
          textController: _promptTextController,
          onClose: close,
          onSubmit: () async {
            final success = await _controller.submitPrompt();
            if (success &&
                !isExpanded &&
                sheetContext != null &&
                sheetContext.mounted) {
              Navigator.of(sheetContext).pop();
            }
          },
        );
      case EditorTool.styles:
        return StylesPanel(
          controller: _controller,
          onClose: close,
          scrollController: scrollController,
          onApply: (style) async {
            final success = await _controller.applyStyle(style);
            if (success &&
                !isExpanded &&
                sheetContext != null &&
                sheetContext.mounted) {
              Navigator.of(sheetContext).pop();
            }
          },
        );
      case EditorTool.reference:
        return ReferencePanel(
          controller: _controller,
          onClose: close,
          onPickReference: _pickReference,
          onSubmit: () async {
            final success = await _controller.submitReference();
            if (success &&
                !isExpanded &&
                sheetContext != null &&
                sheetContext.mounted) {
              Navigator.of(sheetContext).pop();
            }
          },
        );
      case EditorTool.manual:
        return ManualPanel(
          controller: _controller,
          onClose: close,
          scrollController: scrollController,
        );
      case EditorTool.history:
        return HistoryPanel(
          controller: _controller,
          onClose: close,
          scrollController: scrollController,
          onSelect: _selectHistoryItem,
          onSelectOriginal: _selectOriginalBase,
        );
    }
  }

  Future<void> _openDetails(bool isExpanded) async {
    if (isExpanded) {
      _controller.setActiveTool(null);
      setState(() => _detailsInspectorOpen = true);
      return;
    }

    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (sheetContext) {
        return SizedBox(
          height: MediaQuery.sizeOf(sheetContext).height * 0.72,
          child: AnimatedBuilder(
            animation: _controller,
            builder: (_, _) => EditDetailsPanel(
              controller: _controller,
              onClose: () => Navigator.of(sheetContext).pop(),
            ),
          ),
        );
      },
    );
  }

  Future<void> _selectHistoryItem(EditHistoryItem item) async {
    if (_controller.selectHistoryItem(item)) {
      return;
    }
    final discard = await showDialog<bool>(
      context: context,
      builder: (dialogContext) {
        final l10n = dialogContext.l10n;
        return AlertDialog(
          title: Text(l10n.discardDraftTitle),
          content: Text(l10n.discardDraftForHistory),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(false),
              child: Text(l10n.actionBack),
            ),
            FilledButton(
              onPressed: () => Navigator.of(dialogContext).pop(true),
              child: Text(l10n.actionDiscardAndSwitch),
            ),
          ],
        );
      },
    );
    if (discard == true) {
      _controller.selectHistoryItem(item, discardDraft: true);
    }
  }

  Future<void> _selectOriginalBase() async {
    if (_controller.selectOriginalAsBase()) {
      return;
    }
    final discard = await showDialog<bool>(
      context: context,
      builder: (dialogContext) {
        final l10n = dialogContext.l10n;
        return AlertDialog(
          title: Text(l10n.discardDraftTitle),
          content: Text(l10n.discardDraftForOriginal),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(false),
              child: Text(l10n.actionBack),
            ),
            FilledButton(
              onPressed: () => Navigator.of(dialogContext).pop(true),
              child: Text(l10n.actionDiscardAndSwitch),
            ),
          ],
        );
      },
    );
    if (discard == true) {
      _controller.selectOriginalAsBase(discardDraft: true);
    }
  }

  Future<void> _pickOriginal() async {
    if (_controller.sessionId != null) {
      final replace = await showDialog<bool>(
        context: context,
        builder: (dialogContext) {
          final l10n = dialogContext.l10n;
          return AlertDialog(
            title: Text(l10n.replaceOriginalTitle),
            content: Text(l10n.replaceOriginalMessage),
            actions: [
              TextButton(
                onPressed: () => Navigator.of(dialogContext).pop(false),
                child: Text(l10n.actionCancel),
              ),
              FilledButton(
                onPressed: () => Navigator.of(dialogContext).pop(true),
                child: Text(l10n.actionReplaceImage),
              ),
            ],
          );
        },
      );
      if (replace != true || !mounted) {
        return;
      }
    }
    final bytes = await _pickImageBytes();
    if (bytes != null) {
      _controller.setOriginalImage(bytes);
    }
  }

  Future<void> _pickReference() async {
    final bytes = await _pickImageBytes();
    if (bytes != null) {
      _controller.setReferenceImage(bytes);
    }
  }

  Future<Uint8List?> _pickImageBytes() async {
    try {
      final file = await _imagePicker.pickImage(source: ImageSource.gallery);
      return file == null ? null : await file.readAsBytes();
    } catch (error) {
      if (mounted) {
        final l10n = context.l10n;
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(l10n.imagePickFailed(error.runtimeType.toString())),
          ),
        );
      }
      return null;
    }
  }

  Future<void> _confirmClearWorkspace() async {
    final clear = await showDialog<bool>(
      context: context,
      builder: (dialogContext) {
        final l10n = dialogContext.l10n;
        return AlertDialog(
          title: Text(l10n.clearWorkTitle),
          content: Text(l10n.clearWorkMessage),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(false),
              child: Text(l10n.actionCancel),
            ),
            FilledButton(
              onPressed: () => Navigator.of(dialogContext).pop(true),
              child: Text(l10n.actionClearScreen),
            ),
          ],
        );
      },
    );
    if (clear == true) {
      _controller.setPromptDraft('');
      _controller.clearOriginalImage();
    }
  }
}

class _DesktopInspector extends StatelessWidget {
  const _DesktopInspector({required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    final colors = context.editorColors;
    return AnimatedContainer(
      key: const Key('desktop_inspector'),
      duration: const Duration(milliseconds: 180),
      width: 380,
      decoration: BoxDecoration(
        color: colors.surface,
        border: Border(left: BorderSide(color: colors.border)),
      ),
      child: child,
    );
  }
}
