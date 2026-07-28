import 'package:flutter/material.dart';

import 'app_theme.dart';
import 'editor_controller.dart';

class EditorToolDock extends StatelessWidget {
  const EditorToolDock({
    super.key,
    required this.selectedTool,
    required this.historyCount,
    required this.onSelected,
  });

  final EditorTool? selectedTool;
  final int historyCount;
  final ValueChanged<EditorTool> onSelected;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      key: const Key('editor_tool_dock'),
      decoration: const BoxDecoration(
        color: AppColors.surface,
        border: Border(top: BorderSide(color: AppColors.border)),
      ),
      child: SafeArea(
        top: false,
        child: SizedBox(
          height: 72,
          child: Row(
            children: [
              _ToolItem(
                key: const Key('tool_prompt'),
                tool: EditorTool.prompt,
                label: '指令',
                icon: Icons.auto_awesome_outlined,
                selected: selectedTool == EditorTool.prompt,
                onTap: onSelected,
              ),
              _ToolItem(
                key: const Key('tool_styles'),
                tool: EditorTool.styles,
                label: '風格',
                icon: Icons.palette_outlined,
                selected: selectedTool == EditorTool.styles,
                onTap: onSelected,
              ),
              _ToolItem(
                key: const Key('tool_reference'),
                tool: EditorTool.reference,
                label: '參考',
                icon: Icons.photo_outlined,
                selected: selectedTool == EditorTool.reference,
                onTap: onSelected,
              ),
              _ToolItem(
                key: const Key('tool_manual'),
                tool: EditorTool.manual,
                label: '調整',
                icon: Icons.tune,
                selected: selectedTool == EditorTool.manual,
                onTap: onSelected,
              ),
              _ToolItem(
                key: const Key('tool_history'),
                tool: EditorTool.history,
                label: '歷史',
                icon: Icons.history,
                selected: selectedTool == EditorTool.history,
                badge: historyCount == 0 ? null : historyCount.toString(),
                onTap: onSelected,
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _ToolItem extends StatelessWidget {
  const _ToolItem({
    super.key,
    required this.tool,
    required this.label,
    required this.icon,
    required this.selected,
    required this.onTap,
    this.badge,
  });

  final EditorTool tool;
  final String label;
  final IconData icon;
  final bool selected;
  final String? badge;
  final ValueChanged<EditorTool> onTap;

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: Semantics(
        button: true,
        selected: selected,
        label: label,
        child: InkWell(
          onTap: () => onTap(tool),
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 160),
            decoration: BoxDecoration(
              border: Border(
                top: BorderSide(
                  color: selected ? AppColors.accent : Colors.transparent,
                  width: 2,
                ),
              ),
            ),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Stack(
                  clipBehavior: Clip.none,
                  children: [
                    Icon(
                      icon,
                      size: 23,
                      color: selected
                          ? AppColors.accentBright
                          : AppColors.textSecondary,
                    ),
                    if (badge != null)
                      Positioned(
                        right: -12,
                        top: -7,
                        child: Container(
                          constraints: const BoxConstraints(minWidth: 18),
                          padding: const EdgeInsets.symmetric(
                            horizontal: 5,
                            vertical: 2,
                          ),
                          decoration: BoxDecoration(
                            color: AppColors.accent,
                            borderRadius: BorderRadius.circular(999),
                            border: Border.all(
                              color: AppColors.surface,
                              width: 1.5,
                            ),
                          ),
                          child: Text(
                            badge!,
                            textAlign: TextAlign.center,
                            style: const TextStyle(
                              color: Colors.white,
                              fontSize: 10,
                              height: 1.2,
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                        ),
                      ),
                  ],
                ),
                const SizedBox(height: 5),
                Text(
                  label,
                  style: TextStyle(
                    color: selected
                        ? AppColors.accentBright
                        : AppColors.textSecondary,
                    fontSize: 12,
                    fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
