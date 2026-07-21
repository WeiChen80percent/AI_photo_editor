import 'dart:typed_data';

import 'package:flutter/material.dart';

import 'app_theme.dart';
import 'edit_models.dart';
import 'editor_controller.dart';

class PromptPanel extends StatelessWidget {
  const PromptPanel({
    super.key,
    required this.controller,
    required this.textController,
    required this.onClose,
    required this.onSubmit,
  });

  final EditorController controller;
  final TextEditingController textController;
  final VoidCallback onClose;
  final Future<void> Function() onSubmit;

  @override
  Widget build(BuildContext context) {
    return PanelScaffold(
      title: '指令修圖',
      subtitle: controller.selectedEdit == null ? '從原圖建立第一個版本' : '從目前選中的版本繼續調整',
      icon: Icons.auto_awesome_outlined,
      onClose: onClose,
      message: controller.errorMessage,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          TextField(
            key: const Key('prompt_input'),
            controller: textController,
            autofocus: true,
            minLines: 2,
            maxLines: 4,
            textInputAction: TextInputAction.done,
            onChanged: controller.setPromptDraft,
            onSubmitted: (_) {
              if (controller.canSubmitPrompt) {
                onSubmit();
              }
            },
            decoration: const InputDecoration(hintText: '例如：人物亮一點、天空暗一點、不要太鮮豔'),
          ),
          const SizedBox(height: AppSpacing.sm),
          const Text(
            '指令與參考圖是兩種獨立模式；這裡只會送出文字。',
            style: TextStyle(color: AppColors.textMuted, fontSize: 12),
          ),
          const SizedBox(height: AppSpacing.lg),
          FilledButton.icon(
            key: const Key('submit_prompt_button'),
            onPressed: controller.canSubmitPrompt ? onSubmit : null,
            icon: controller.isProcessing
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(
                      strokeWidth: 2,
                      color: Colors.white,
                    ),
                  )
                : const Icon(Icons.auto_fix_high),
            label: Text(controller.isProcessing ? '處理中…' : '套用指令'),
          ),
        ],
      ),
    );
  }
}

class ReferencePanel extends StatelessWidget {
  const ReferencePanel({
    super.key,
    required this.controller,
    required this.onClose,
    required this.onPickReference,
    required this.onSubmit,
  });

  final EditorController controller;
  final VoidCallback onClose;
  final VoidCallback onPickReference;
  final Future<void> Function() onSubmit;

  @override
  Widget build(BuildContext context) {
    return PanelScaffold(
      title: '參考圖修圖',
      subtitle: controller.selectedEdit == null
          ? '原圖會依參考圖的色彩方向調整'
          : '從目前版本套用參考圖方向',
      icon: Icons.photo_outlined,
      onClose: onClose,
      message: controller.errorMessage,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _ReferencePreview(bytes: controller.referenceImageBytes),
          const SizedBox(height: AppSpacing.md),
          Row(
            children: [
              Expanded(
                child: OutlinedButton.icon(
                  key: const Key('pick_reference_button'),
                  onPressed: onPickReference,
                  icon: const Icon(Icons.photo_library_outlined),
                  label: Text(
                    controller.referenceImageBytes == null ? '選擇參考圖' : '更換參考圖',
                  ),
                ),
              ),
              if (controller.referenceImageBytes != null) ...[
                const SizedBox(width: AppSpacing.xs),
                IconButton.outlined(
                  key: const Key('clear_reference_button'),
                  tooltip: '移除參考圖',
                  onPressed: controller.clearReferenceImage,
                  icon: const Icon(Icons.delete_outline),
                ),
              ],
            ],
          ),
          const SizedBox(height: AppSpacing.lg),
          FilledButton.icon(
            key: const Key('submit_reference_button'),
            onPressed: controller.canSubmitReference ? onSubmit : null,
            icon: controller.isProcessing
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(
                      strokeWidth: 2,
                      color: Colors.white,
                    ),
                  )
                : const Icon(Icons.auto_fix_high),
            label: Text(controller.isProcessing ? '處理中…' : '套用參考圖'),
          ),
        ],
      ),
    );
  }
}

class ManualPanel extends StatelessWidget {
  const ManualPanel({
    super.key,
    required this.controller,
    required this.onClose,
    this.scrollController,
  });

  final EditorController controller;
  final VoidCallback onClose;
  final ScrollController? scrollController;

  @override
  Widget build(BuildContext context) {
    if (controller.isLoadingManual) {
      return PanelScaffold(
        title: '手動調整',
        icon: Icons.tune,
        onClose: onClose,
        child: const Center(
          child: Padding(
            padding: EdgeInsets.all(32),
            child: CircularProgressIndicator(),
          ),
        ),
      );
    }

    if (!controller.canOpenManual ||
        controller.manualSchema == null ||
        controller.manualSourceEditId == null) {
      return PanelScaffold(
        title: '手動調整',
        icon: Icons.tune,
        onClose: onClose,
        message: controller.errorMessage,
        child: _UnavailablePanel(
          icon: Icons.tune,
          message: controller.manualDisabledReason,
        ),
      );
    }

    final schema = controller.manualSchema!;
    final common = schema.parameters
        .where((item) => item.defaultVisible)
        .toList();
    final advanced = schema.parameters
        .where((item) => !item.defaultVisible)
        .toList();
    final source = controller.selectedEdit;

    return Column(
      key: const Key('manual_panel'),
      children: [
        PanelHeader(
          title: '手動調整',
          subtitle: source == null
              ? null
              : '來源版本 · ${source.targetLabel} · ${source.modeLabel}',
          icon: Icons.tune,
          onClose: onClose,
          trailing: controller.manualIsDirty ? const _DraftStatus() : null,
        ),
        if (controller.errorMessage != null)
          PanelMessage(message: controller.errorMessage!, isError: true),
        Expanded(
          child: ListView(
            controller: scrollController,
            padding: const EdgeInsets.fromLTRB(
              AppSpacing.md,
              AppSpacing.sm,
              AppSpacing.md,
              AppSpacing.lg,
            ),
            children: [
              for (final spec in common)
                ManualSlider(
                  spec: spec,
                  value: controller.manualValues[spec.key] ?? spec.neutral,
                  onChanged: (value) => controller.setManualValue(spec, value),
                  onReset: () => controller.resetManualParameter(spec),
                ),
              const SizedBox(height: AppSpacing.xs),
              Material(
                color: Colors.transparent,
                child: InkWell(
                  key: const Key('manual_advanced_toggle'),
                  borderRadius: BorderRadius.circular(AppRadii.small),
                  onTap: () => controller.setManualAdvancedExpanded(
                    !controller.manualAdvancedExpanded,
                  ),
                  child: Padding(
                    padding: const EdgeInsets.symmetric(
                      horizontal: AppSpacing.xs,
                      vertical: AppSpacing.sm,
                    ),
                    child: Row(
                      children: [
                        const Expanded(
                          child: Text(
                            '進階調整',
                            style: TextStyle(fontWeight: FontWeight.w600),
                          ),
                        ),
                        Icon(
                          controller.manualAdvancedExpanded
                              ? Icons.expand_less
                              : Icons.expand_more,
                          color: AppColors.textSecondary,
                        ),
                      ],
                    ),
                  ),
                ),
              ),
              AnimatedCrossFade(
                duration: const Duration(milliseconds: 180),
                firstChild: const SizedBox.shrink(),
                secondChild: Column(
                  children: [
                    for (final spec in advanced)
                      ManualSlider(
                        spec: spec,
                        value:
                            controller.manualValues[spec.key] ?? spec.neutral,
                        onChanged: (value) =>
                            controller.setManualValue(spec, value),
                        onReset: () => controller.resetManualParameter(spec),
                      ),
                  ],
                ),
                crossFadeState: controller.manualAdvancedExpanded
                    ? CrossFadeState.showSecond
                    : CrossFadeState.showFirst,
              ),
            ],
          ),
        ),
        _ManualActions(controller: controller),
      ],
    );
  }
}

class HistoryPanel extends StatelessWidget {
  const HistoryPanel({
    super.key,
    required this.controller,
    required this.onClose,
    required this.onSelect,
    this.scrollController,
  });

  final EditorController controller;
  final VoidCallback onClose;
  final Future<void> Function(EditHistoryItem item) onSelect;
  final ScrollController? scrollController;

  @override
  Widget build(BuildContext context) {
    final items = controller.history.reversed.toList();
    return Column(
      key: const Key('history_panel'),
      children: [
        PanelHeader(
          title: '歷史紀錄',
          subtitle: '版本 ${controller.history.length}',
          icon: Icons.history,
          onClose: onClose,
          trailing: IconButton(
            key: const Key('refresh_history_button'),
            tooltip: '重新同步',
            onPressed: controller.sessionId == null
                ? null
                : () => controller.refreshHistory(),
            icon: const Icon(Icons.refresh, size: 20),
          ),
        ),
        if (controller.errorMessage != null)
          PanelMessage(message: controller.errorMessage!, isError: true),
        Expanded(
          child: items.isEmpty
              ? const _UnavailablePanel(
                  icon: Icons.history,
                  message: '完成第一次修圖後，版本會依序顯示在這裡。',
                )
              : ListView.separated(
                  controller: scrollController,
                  padding: const EdgeInsets.fromLTRB(
                    AppSpacing.md,
                    AppSpacing.sm,
                    AppSpacing.md,
                    AppSpacing.lg,
                  ),
                  itemCount: items.length,
                  separatorBuilder: (_, _) =>
                      const SizedBox(height: AppSpacing.xs),
                  itemBuilder: (context, index) {
                    final item = items[index];
                    final originalIndex = controller.history.indexWhere(
                      (entry) => entry.editId == item.editId,
                    );
                    return HistoryTile(
                      key: Key('history_${item.editId}'),
                      item: item,
                      version: originalIndex + 1,
                      selected: item.editId == controller.selectedEditId,
                      onTap: () => onSelect(item),
                    );
                  },
                ),
        ),
      ],
    );
  }
}

class EditDetailsPanel extends StatelessWidget {
  const EditDetailsPanel({
    super.key,
    required this.controller,
    required this.onClose,
  });

  final EditorController controller;
  final VoidCallback onClose;

  @override
  Widget build(BuildContext context) {
    final edit = controller.selectedEdit;
    if (edit == null) {
      return const SizedBox.shrink();
    }
    final parameters = controller.currentParameters.entries
        .where((entry) => parameterLabels.containsKey(entry.key))
        .toList();
    return PanelScaffold(
      title: controller.hasUncommittedPreview ? '目前預覽' : '目前調整',
      subtitle: '${edit.targetLabel} · ${edit.modeLabel}',
      icon: Icons.info_outline,
      onClose: onClose,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (edit.explanation != null) ...[
            Text(
              edit.explanation!,
              style: const TextStyle(color: AppColors.textSecondary),
            ),
            const SizedBox(height: AppSpacing.md),
          ],
          for (final entry in parameters)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 7),
              child: Row(
                children: [
                  Expanded(
                    child: Text(
                      parameterLabels[entry.key]!,
                      style: const TextStyle(color: AppColors.textSecondary),
                    ),
                  ),
                  Text(
                    entry.value.toString(),
                    style: const TextStyle(
                      fontWeight: FontWeight.w600,
                      fontFeatures: [FontFeature.tabularFigures()],
                    ),
                  ),
                ],
              ),
            ),
          if (parameters.isEmpty)
            const Text(
              '這個版本沒有可顯示的手動參數。',
              style: TextStyle(color: AppColors.textMuted),
            ),
        ],
      ),
    );
  }
}

class PanelScaffold extends StatelessWidget {
  const PanelScaffold({
    super.key,
    required this.title,
    required this.icon,
    required this.onClose,
    required this.child,
    this.subtitle,
    this.message,
  });

  final String title;
  final String? subtitle;
  final IconData icon;
  final VoidCallback onClose;
  final Widget child;
  final String? message;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        PanelHeader(
          title: title,
          subtitle: subtitle,
          icon: icon,
          onClose: onClose,
        ),
        if (message != null) PanelMessage(message: message!, isError: true),
        Expanded(
          child: SingleChildScrollView(
            padding: const EdgeInsets.fromLTRB(
              AppSpacing.md,
              AppSpacing.sm,
              AppSpacing.md,
              AppSpacing.lg,
            ),
            child: child,
          ),
        ),
      ],
    );
  }
}

class PanelHeader extends StatelessWidget {
  const PanelHeader({
    super.key,
    required this.title,
    required this.icon,
    required this.onClose,
    this.subtitle,
    this.trailing,
  });

  final String title;
  final String? subtitle;
  final IconData icon;
  final VoidCallback onClose;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: const BoxDecoration(
        border: Border(bottom: BorderSide(color: AppColors.border)),
      ),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(
          AppSpacing.md,
          AppSpacing.sm,
          AppSpacing.xs,
          AppSpacing.sm,
        ),
        child: Row(
          children: [
            Icon(icon, size: 21, color: AppColors.accentBright),
            const SizedBox(width: AppSpacing.sm),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title, style: Theme.of(context).textTheme.titleMedium),
                  if (subtitle != null) ...[
                    const SizedBox(height: 2),
                    Text(
                      subtitle!,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        color: AppColors.textMuted,
                        fontSize: 11,
                      ),
                    ),
                  ],
                ],
              ),
            ),
            ?trailing,
            IconButton(
              key: const Key('close_panel_button'),
              tooltip: '收合',
              onPressed: onClose,
              icon: const Icon(Icons.close, size: 21),
            ),
          ],
        ),
      ),
    );
  }
}

class PanelMessage extends StatelessWidget {
  const PanelMessage({super.key, required this.message, required this.isError});

  final String message;
  final bool isError;

  @override
  Widget build(BuildContext context) {
    return Container(
      key: Key(isError ? 'panel_error' : 'panel_status'),
      margin: const EdgeInsets.fromLTRB(
        AppSpacing.md,
        AppSpacing.sm,
        AppSpacing.md,
        0,
      ),
      padding: const EdgeInsets.all(AppSpacing.sm),
      decoration: BoxDecoration(
        color: isError
            ? AppColors.error.withValues(alpha: 0.1)
            : AppColors.success.withValues(alpha: 0.1),
        borderRadius: BorderRadius.circular(AppRadii.small),
        border: Border.all(
          color: isError
              ? AppColors.error.withValues(alpha: 0.35)
              : AppColors.success.withValues(alpha: 0.35),
        ),
      ),
      child: Row(
        children: [
          Icon(
            isError ? Icons.error_outline : Icons.check_circle_outline,
            color: isError ? AppColors.error : AppColors.success,
            size: 19,
          ),
          const SizedBox(width: AppSpacing.xs),
          Expanded(
            child: Text(
              message,
              style: TextStyle(
                color: isError ? AppColors.error : AppColors.success,
                fontSize: 12,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class ManualSlider extends StatelessWidget {
  const ManualSlider({
    super.key,
    required this.spec,
    required this.value,
    required this.onChanged,
    required this.onReset,
  });

  final ManualParameterSpec spec;
  final double value;
  final ValueChanged<double> onChanged;
  final VoidCallback onReset;

  @override
  Widget build(BuildContext context) {
    final divisions = ((spec.maximum - spec.minimum) / spec.step).round();
    return Padding(
      key: Key('manual_slider_${spec.key}'),
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.xs),
      child: Column(
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  spec.label,
                  style: const TextStyle(fontWeight: FontWeight.w500),
                ),
              ),
              Text(
                spec.format(value),
                key: Key('manual_value_${spec.key}'),
                style: const TextStyle(
                  color: AppColors.textSecondary,
                  fontSize: 13,
                  fontWeight: FontWeight.w600,
                  fontFeatures: [FontFeature.tabularFigures()],
                ),
              ),
              const SizedBox(width: 2),
              IconButton(
                tooltip: '將${spec.label}設為中性值',
                onPressed: onReset,
                visualDensity: VisualDensity.compact,
                icon: const Icon(Icons.restart_alt, size: 18),
              ),
            ],
          ),
          Slider(
            value: value.clamp(spec.minimum, spec.maximum),
            min: spec.minimum,
            max: spec.maximum,
            divisions: divisions > 0 && divisions <= 1000 ? divisions : null,
            onChanged: onChanged,
          ),
        ],
      ),
    );
  }
}

class HistoryTile extends StatelessWidget {
  const HistoryTile({
    super.key,
    required this.item,
    required this.version,
    required this.selected,
    required this.onTap,
  });

  final EditHistoryItem item;
  final int version;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final summary = compactParameterSummary(item.parameters);
    return Material(
      color: selected ? AppColors.accentSoft : AppColors.surfaceRaised,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppRadii.small),
        side: BorderSide(color: selected ? AppColors.accent : AppColors.border),
      ),
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(AppSpacing.sm),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              ClipRRect(
                borderRadius: BorderRadius.circular(AppRadii.small),
                child: Image.network(
                  item.resultUrl,
                  width: 64,
                  height: 64,
                  fit: BoxFit.cover,
                  errorBuilder: (_, _, _) => const SizedBox(
                    width: 64,
                    height: 64,
                    child: ColoredBox(
                      color: AppColors.surfaceSoft,
                      child: Icon(
                        Icons.broken_image_outlined,
                        color: AppColors.textMuted,
                      ),
                    ),
                  ),
                ),
              ),
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            '版本 $version · ${item.modeLabel}',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              fontWeight: FontWeight.w700,
                              fontSize: 13,
                            ),
                          ),
                        ),
                        if (selected)
                          const Icon(
                            Icons.check_circle,
                            size: 18,
                            color: AppColors.accentBright,
                          ),
                      ],
                    ),
                    const SizedBox(height: 4),
                    Text(
                      item.displayTitle,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        color: AppColors.textSecondary,
                        fontSize: 12,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      '${item.targetLabel}${summary.isEmpty ? '' : ' · $summary'}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        color: AppColors.textMuted,
                        fontSize: 11,
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _ReferencePreview extends StatelessWidget {
  const _ReferencePreview({required this.bytes});

  final Uint8List? bytes;

  @override
  Widget build(BuildContext context) {
    return AspectRatio(
      aspectRatio: 16 / 9,
      child: ClipRRect(
        borderRadius: BorderRadius.circular(AppRadii.medium),
        child: DecoratedBox(
          decoration: BoxDecoration(
            color: AppColors.imageStage,
            border: Border.all(color: AppColors.border),
            borderRadius: BorderRadius.circular(AppRadii.medium),
          ),
          child: bytes == null
              ? const Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(
                        Icons.add_photo_alternate_outlined,
                        color: AppColors.textMuted,
                      ),
                      SizedBox(height: 8),
                      Text(
                        '尚未選擇參考圖',
                        style: TextStyle(color: AppColors.textMuted),
                      ),
                    ],
                  ),
                )
              : Image.memory(bytes!, fit: BoxFit.contain),
        ),
      ),
    );
  }
}

class _ManualActions extends StatelessWidget {
  const _ManualActions({required this.controller});

  final EditorController controller;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: const BoxDecoration(
        color: AppColors.surface,
        border: Border(top: BorderSide(color: AppColors.border)),
      ),
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.all(AppSpacing.md),
          child: Row(
            children: [
              Expanded(
                child: OutlinedButton.icon(
                  key: const Key('reset_all_manual_button'),
                  onPressed: controller.manualIsDirty
                      ? controller.resetAllManual
                      : null,
                  icon: const Icon(Icons.restart_alt),
                  label: const Text('重設'),
                ),
              ),
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                child: FilledButton.icon(
                  key: const Key('commit_manual_button'),
                  onPressed:
                      controller.manualIsDirty &&
                          !controller.isCommittingManual &&
                          !controller.isPreviewing
                      ? controller.commitManual
                      : null,
                  icon: controller.isCommittingManual
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(
                            strokeWidth: 2,
                            color: Colors.white,
                          ),
                        )
                      : const Icon(Icons.check),
                  label: Text(controller.isCommittingManual ? '套用中…' : '套用'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _DraftStatus extends StatelessWidget {
  const _DraftStatus();

  @override
  Widget build(BuildContext context) {
    return const Padding(
      padding: EdgeInsets.symmetric(horizontal: AppSpacing.xs),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.circle, size: 7, color: AppColors.warning),
          SizedBox(width: 5),
          Text(
            '尚未套用',
            style: TextStyle(
              color: AppColors.warning,
              fontSize: 11,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }
}

class _UnavailablePanel extends StatelessWidget {
  const _UnavailablePanel({required this.icon, required this.message});

  final IconData icon;
  final String message;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.xl),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 36, color: AppColors.textMuted),
            const SizedBox(height: AppSpacing.md),
            Text(
              message,
              textAlign: TextAlign.center,
              style: const TextStyle(color: AppColors.textSecondary),
            ),
          ],
        ),
      ),
    );
  }
}
