import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

import 'api_service.dart';
import 'app_theme.dart';
import 'edit_models.dart';
import 'editor_canvas.dart';
import 'editor_controller.dart';
import 'editor_panels.dart';
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
  late final TextEditingController _promptController;

  @override
  void initState() {
    super.initState();
    _ownsController = widget.controller == null;
    _controller = widget.controller ?? EditorController(api: ApiService());
    _imagePicker = widget.imagePicker ?? ImagePicker();
    _promptController = TextEditingController(text: _controller.promptDraft);
  }

  @override
  void dispose() {
    _promptController.dispose();
    if (_ownsController) {
      _controller.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, _) {
        return Scaffold(
          appBar: AppBar(
            toolbarHeight: 58,
            title: const Text('AI 修圖'),
            leading: _controller.sessionId == null
                ? const Padding(
                    padding: EdgeInsets.all(14),
                    child: Icon(
                      Icons.auto_fix_high,
                      color: AppColors.accentBright,
                    ),
                  )
                : IconButton(
                    tooltip: '清除目前工作',
                    onPressed: _confirmClearWorkspace,
                    icon: const Icon(Icons.close),
                  ),
            actions: [
              IconButton(
                key: const Key('appbar_pick_original'),
                tooltip: _controller.hasOriginal ? '更換原圖' : '選擇原圖',
                onPressed: _pickOriginal,
                icon: const Icon(Icons.add_photo_alternate_outlined),
              ),
              const SizedBox(width: 4),
            ],
          ),
          body: LayoutBuilder(
            builder: (context, constraints) {
              final width = constraints.maxWidth;
              final isCompact = width < 600;
              final isExpanded = width >= 1024;
              final isLandscape =
                  MediaQuery.orientationOf(context) == Orientation.landscape;
              final sideBySide =
                  isExpanded || (!isCompact && isLandscape && width >= 760);
              return Column(
                children: [
                  Expanded(
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Expanded(
                          child: Padding(
                            padding: EdgeInsets.fromLTRB(
                              isCompact ? AppSpacing.sm : AppSpacing.md,
                              isCompact ? AppSpacing.sm : AppSpacing.md,
                              isExpanded && _controller.activeTool != null
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
                        if (isExpanded && _controller.activeTool != null)
                          _DesktopInspector(
                            child: _panelForTool(
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
    if (isExpanded && _controller.activeTool == tool) {
      _controller.setActiveTool(null);
      return;
    }

    _controller.setActiveTool(tool);
    if (tool == EditorTool.manual) {
      await _controller.openManual();
    }
    if (!mounted || isExpanded) {
      return;
    }

    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      backgroundColor: AppColors.surface,
      barrierColor: Colors.black54,
      builder: (sheetContext) {
        final initial = tool == EditorTool.manual || tool == EditorTool.history
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
                          color: AppColors.borderStrong,
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
    if (mounted && _controller.activeTool == tool) {
      _controller.setActiveTool(null);
    }
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
          textController: _promptController,
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
        );
    }
  }

  Future<void> _openDetails(bool isExpanded) async {
    if (isExpanded) {
      await showDialog<void>(
        context: context,
        builder: (dialogContext) {
          return Dialog(
            backgroundColor: AppColors.surface,
            child: SizedBox(
              width: 420,
              height: 560,
              child: AnimatedBuilder(
                animation: _controller,
                builder: (_, _) => EditDetailsPanel(
                  controller: _controller,
                  onClose: () => Navigator.of(dialogContext).pop(),
                ),
              ),
            ),
          );
        },
      );
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
        return AlertDialog(
          title: const Text('捨棄尚未套用的調整？'),
          content: const Text('切換歷史版本會捨棄目前手動調整草稿。'),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(false),
              child: const Text('返回'),
            ),
            FilledButton(
              onPressed: () => Navigator.of(dialogContext).pop(true),
              child: const Text('捨棄並切換'),
            ),
          ],
        );
      },
    );
    if (discard == true) {
      _controller.selectHistoryItem(item, discardDraft: true);
    }
  }

  Future<void> _pickOriginal() async {
    if (_controller.sessionId != null) {
      final replace = await showDialog<bool>(
        context: context,
        builder: (dialogContext) {
          return AlertDialog(
            title: const Text('更換原始圖片？'),
            content: const Text('更換後會清除目前 session 與未套用的手動草稿。'),
            actions: [
              TextButton(
                onPressed: () => Navigator.of(dialogContext).pop(false),
                child: const Text('取消'),
              ),
              FilledButton(
                onPressed: () => Navigator.of(dialogContext).pop(true),
                child: const Text('更換圖片'),
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
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('無法選擇圖片：$error')));
      }
      return null;
    }
  }

  Future<void> _confirmClearWorkspace() async {
    final clear = await showDialog<bool>(
      context: context,
      builder: (dialogContext) {
        return AlertDialog(
          title: const Text('清除目前工作？'),
          content: const Text('畫面會回到初始狀態，後端已保存的歷史不會被刪除。'),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(false),
              child: const Text('取消'),
            ),
            FilledButton(
              onPressed: () => Navigator.of(dialogContext).pop(true),
              child: const Text('清除畫面'),
            ),
          ],
        );
      },
    );
    if (clear == true) {
      _promptController.clear();
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
    return AnimatedContainer(
      key: const Key('desktop_inspector'),
      duration: const Duration(milliseconds: 180),
      width: 380,
      decoration: const BoxDecoration(
        color: AppColors.surface,
        border: Border(left: BorderSide(color: AppColors.border)),
      ),
      child: child,
    );
  }
}
