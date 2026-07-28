import 'dart:typed_data';

import 'package:flutter/gestures.dart';
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
      subtitle: controller.selectedEdit == null
          ? controller.isOriginalBaseSelected && controller.history.isNotEmpty
                ? '從原圖建立新的歷史分支'
                : '從原圖建立第一個版本'
          : '從目前選中的版本繼續調整',
      icon: Icons.auto_awesome_outlined,
      onClose: onClose,
      message: controller.errorMessage ?? controller.statusMessage,
      messageIsError: controller.errorMessage != null,
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

class StylesPanel extends StatelessWidget {
  const StylesPanel({
    super.key,
    required this.controller,
    required this.onClose,
    required this.onApply,
    this.scrollController,
  });

  final EditorController controller;
  final VoidCallback onClose;
  final Future<void> Function(StyleCatalogItem style) onApply;
  final ScrollController? scrollController;

  @override
  Widget build(BuildContext context) {
    final catalog = controller.styleCatalog;
    if (controller.isLoadingStyles) {
      return PanelScaffold(
        title: '風格目錄',
        icon: Icons.palette_outlined,
        onClose: onClose,
        child: const Center(
          child: Padding(
            padding: EdgeInsets.all(32),
            child: CircularProgressIndicator(),
          ),
        ),
      );
    }
    if (catalog == null) {
      return PanelScaffold(
        title: '風格目錄',
        icon: Icons.palette_outlined,
        onClose: onClose,
        message: controller.errorMessage,
        messageIsError: true,
        child: const _UnavailablePanel(
          icon: Icons.palette_outlined,
          message: '風格目錄目前無法載入，請確認後端已啟動。',
        ),
      );
    }

    final styles = controller.visibleStyles;
    final families = catalog.families.keys.toList()..sort();
    return Column(
      key: const Key('styles_panel'),
      children: [
        PanelHeader(
          title: '風格目錄',
          subtitle: '${catalog.styleCount} 種已核准風格 · v${catalog.catalogVersion}',
          icon: Icons.palette_outlined,
          onClose: onClose,
        ),
        if (controller.errorMessage != null)
          PanelMessage(message: controller.errorMessage!, isError: true),
        _StyleFamilyFilterBar(
          families: families,
          counts: catalog.families,
          selectedFamily: controller.selectedStyleFamily,
          onSelected: controller.setStyleFamily,
        ),
        Expanded(
          child: ListView.separated(
            controller: scrollController,
            padding: const EdgeInsets.fromLTRB(
              AppSpacing.md,
              AppSpacing.sm,
              AppSpacing.md,
              AppSpacing.lg,
            ),
            itemCount: styles.length,
            separatorBuilder: (_, _) => const SizedBox(height: AppSpacing.sm),
            itemBuilder: (context, index) {
              final style = styles[index];
              return _StyleCatalogCard(
                key: Key('style_${style.styleId}'),
                style: style,
                strength: controller.styleStrengthFor(style),
                processing: controller.isProcessing,
                canApply:
                    controller.hasOriginal || controller.selectedEdit != null,
                onStrengthChanged: (value) =>
                    controller.setStyleStrength(style, value),
                onApply: () => onApply(style),
              );
            },
          ),
        ),
      ],
    );
  }
}

class _StyleFamilyFilterBar extends StatefulWidget {
  const _StyleFamilyFilterBar({
    required this.families,
    required this.counts,
    required this.selectedFamily,
    required this.onSelected,
  });

  final List<String> families;
  final Map<String, int> counts;
  final String? selectedFamily;
  final ValueChanged<String?> onSelected;

  @override
  State<_StyleFamilyFilterBar> createState() => _StyleFamilyFilterBarState();
}

class _StyleFamilyFilterBarState extends State<_StyleFamilyFilterBar> {
  final ScrollController _controller = ScrollController();
  bool _hasOverflow = false;
  bool _canScrollBack = false;
  bool _canScrollForward = false;

  @override
  void initState() {
    super.initState();
    _controller.addListener(_syncScrollState);
    _scheduleScrollStateSync();
  }

  @override
  void didUpdateWidget(covariant _StyleFamilyFilterBar oldWidget) {
    super.didUpdateWidget(oldWidget);
    _scheduleScrollStateSync();
  }

  @override
  void dispose() {
    _controller
      ..removeListener(_syncScrollState)
      ..dispose();
    super.dispose();
  }

  void _scheduleScrollStateSync() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) {
        _syncScrollState();
      }
    });
  }

  void _syncScrollState() {
    if (!_controller.hasClients) {
      return;
    }
    final position = _controller.position;
    final hasOverflow = position.maxScrollExtent > 0.5;
    final canScrollBack = position.pixels > 0.5;
    final canScrollForward = position.pixels < position.maxScrollExtent - 0.5;
    if (hasOverflow != _hasOverflow ||
        canScrollBack != _canScrollBack ||
        canScrollForward != _canScrollForward) {
      setState(() {
        _hasOverflow = hasOverflow;
        _canScrollBack = canScrollBack;
        _canScrollForward = canScrollForward;
      });
    }
  }

  void _scrollBy(double delta) {
    if (!_controller.hasClients || delta == 0) {
      return;
    }
    final target = (_controller.offset + delta)
        .clamp(0.0, _controller.position.maxScrollExtent)
        .toDouble();
    _controller.animateTo(
      target,
      duration: const Duration(milliseconds: 180),
      curve: Curves.easeOutCubic,
    );
  }

  void _handlePointerSignal(PointerSignalEvent event) {
    if (event is! PointerScrollEvent) {
      return;
    }
    final delta = event.scrollDelta.dx.abs() > event.scrollDelta.dy.abs()
        ? event.scrollDelta.dx
        : event.scrollDelta.dy;
    _scrollBy(delta);
  }

  @override
  Widget build(BuildContext context) {
    final dragDevices = <PointerDeviceKind>{
      PointerDeviceKind.touch,
      PointerDeviceKind.mouse,
      PointerDeviceKind.stylus,
      PointerDeviceKind.trackpad,
    };
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.sm,
        AppSpacing.xs,
        AppSpacing.sm,
        0,
      ),
      child: Row(
        children: [
          if (_hasOverflow)
            _FamilyScrollButton(
              key: const Key('style_family_scroll_back'),
              icon: Icons.chevron_left,
              tooltip: '向左查看更多分類',
              onPressed: _canScrollBack ? () => _scrollBy(-220) : null,
            ),
          Expanded(
            child: Listener(
              onPointerSignal: _handlePointerSignal,
              child: Scrollbar(
                controller: _controller,
                thumbVisibility: _hasOverflow,
                interactive: true,
                scrollbarOrientation: ScrollbarOrientation.bottom,
                child: ScrollConfiguration(
                  behavior: ScrollConfiguration.of(
                    context,
                  ).copyWith(dragDevices: dragDevices),
                  child: SingleChildScrollView(
                    key: const Key('style_family_scroll'),
                    controller: _controller,
                    padding: const EdgeInsets.fromLTRB(
                      AppSpacing.xs,
                      0,
                      AppSpacing.xs,
                      AppSpacing.sm,
                    ),
                    scrollDirection: Axis.horizontal,
                    child: Row(
                      children: [
                        ChoiceChip(
                          key: const Key('style_family_all'),
                          label: const Text('全部'),
                          selected: widget.selectedFamily == null,
                          onSelected: (_) => widget.onSelected(null),
                        ),
                        for (final family in widget.families) ...[
                          const SizedBox(width: AppSpacing.xs),
                          ChoiceChip(
                            key: Key('style_family_$family'),
                            label: Text(
                              '${styleFamilyLabel(family)} '
                              '${widget.counts[family]}',
                            ),
                            selected: widget.selectedFamily == family,
                            onSelected: (_) => widget.onSelected(family),
                          ),
                        ],
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ),
          if (_hasOverflow)
            _FamilyScrollButton(
              key: const Key('style_family_scroll_forward'),
              icon: Icons.chevron_right,
              tooltip: '向右查看更多分類',
              onPressed: _canScrollForward ? () => _scrollBy(220) : null,
            ),
        ],
      ),
    );
  }
}

class _FamilyScrollButton extends StatelessWidget {
  const _FamilyScrollButton({
    super.key,
    required this.icon,
    required this.tooltip,
    required this.onPressed,
  });

  final IconData icon;
  final String tooltip;
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) {
    return IconButton(
      tooltip: tooltip,
      visualDensity: VisualDensity.compact,
      constraints: const BoxConstraints.tightFor(width: 32, height: 36),
      padding: EdgeInsets.zero,
      onPressed: onPressed,
      icon: Icon(icon, size: 20),
    );
  }
}

class _StyleCatalogCard extends StatelessWidget {
  const _StyleCatalogCard({
    super.key,
    required this.style,
    required this.strength,
    required this.processing,
    required this.canApply,
    required this.onStrengthChanged,
    required this.onApply,
  });

  final StyleCatalogItem style;
  final double strength;
  final bool processing;
  final bool canApply;
  final ValueChanged<double> onStrengthChanged;
  final VoidCallback onApply;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        color: AppColors.surfaceRaised,
        borderRadius: BorderRadius.circular(AppRadii.medium),
        border: Border.all(color: AppColors.border),
      ),
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.sm),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            ClipRRect(
              borderRadius: BorderRadius.circular(AppRadii.small),
              child: SizedBox(
                width: 72,
                height: 92,
                child: style.previewUrl == null
                    ? const ColoredBox(
                        color: AppColors.surfaceSoft,
                        child: Icon(
                          Icons.palette_outlined,
                          color: AppColors.textMuted,
                        ),
                      )
                    : Image.network(
                        style.previewUrl!,
                        fit: BoxFit.cover,
                        errorBuilder: (_, _, _) => const ColoredBox(
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
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Text(
                          style.displayName,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(fontWeight: FontWeight.w700),
                        ),
                      ),
                      Text(
                        'v${style.version}',
                        style: const TextStyle(
                          color: AppColors.textMuted,
                          fontSize: 11,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 2),
                  Text(
                    '${styleFamilyLabel(style.family)} · ${style.displayNameEn}',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      color: AppColors.textSecondary,
                      fontSize: 11,
                    ),
                  ),
                  const SizedBox(height: AppSpacing.xs),
                  Row(
                    children: [
                      const Text(
                        '強度',
                        style: TextStyle(
                          color: AppColors.textMuted,
                          fontSize: 11,
                        ),
                      ),
                      Expanded(
                        child: Slider(
                          key: Key('style_strength_${style.styleId}'),
                          min: style.minimumStrength,
                          max: style.maximumStrength,
                          divisions: 20,
                          value: strength,
                          semanticFormatterCallback: (value) =>
                              '${(value * 100).round()}%',
                          onChanged: processing ? null : onStrengthChanged,
                        ),
                      ),
                      SizedBox(
                        width: 34,
                        child: Text(
                          '${(strength * 100).round()}%',
                          textAlign: TextAlign.end,
                          style: const TextStyle(fontSize: 11),
                        ),
                      ),
                    ],
                  ),
                  Align(
                    alignment: Alignment.centerRight,
                    child: IconButton.filledTonal(
                      key: Key('apply_style_${style.styleId}'),
                      tooltip: '套用風格',
                      onPressed: processing || !canApply ? null : onApply,
                      icon: const Icon(Icons.auto_fix_high, size: 17),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
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
    required this.onSelectOriginal,
    this.scrollController,
  });

  final EditorController controller;
  final VoidCallback onClose;
  final Future<void> Function(EditHistoryItem item) onSelect;
  final Future<void> Function() onSelectOriginal;
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
        if (items.isNotEmpty)
          Padding(
            padding: const EdgeInsets.fromLTRB(
              AppSpacing.md,
              AppSpacing.sm,
              AppSpacing.md,
              0,
            ),
            child: OutlinedButton.icon(
              key: const Key('history_original_branch'),
              onPressed: onSelectOriginal,
              icon: const Icon(Icons.account_tree_outlined, size: 18),
              label: Text(
                controller.isOriginalBaseSelected
                    ? '已選原圖 · 下一次建立新分支'
                    : '從原圖建立新分支',
              ),
            ),
          ),
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
                    final parentIndex = item.parentEditId == null
                        ? -1
                        : controller.history.indexWhere(
                            (entry) => entry.editId == item.parentEditId,
                          );
                    return HistoryTile(
                      key: Key('history_${item.editId}'),
                      item: item,
                      metadataCatalog: controller.metadataCatalogFor(item),
                      version: originalIndex + 1,
                      parentVersion: parentIndex < 0 ? null : parentIndex + 1,
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
    final metadataCatalog = controller.metadataCatalogFor(edit);
    final adaptiveOperations = edit.adaptiveOperationsFor(metadataCatalog);
    final displayedParameters = controller.hasUncommittedPreview
        ? controller.currentParameters
        : edit.parametersForDisplay(metadataCatalog);
    final parameters = displayedParameters.entries
        .where(
          (entry) => entry.value is num && metadataCatalog.contains(entry.key),
        )
        .toList();
    return PanelScaffold(
      title: controller.hasUncommittedPreview ? '目前預覽' : '目前調整',
      subtitle: '${edit.targetLabel} · ${edit.modeLabel}',
      icon: Icons.info_outline,
      onClose: onClose,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (edit.style != null) ...[
            _StyleDetails(style: edit.style!),
            const SizedBox(height: AppSpacing.md),
          ],
          if (edit.isDirectStyleEdit && !controller.hasUncommittedPreview) ...[
            Text(
              '以下為依 ${(edit.style!.strength * 100).round()}% 強度折算的'
              '等效參數；風格實際效果也包含曲線、分色與其他內部配方。',
              key: const Key('style_effective_parameter_note'),
              style: const TextStyle(color: AppColors.textMuted, fontSize: 11),
            ),
            const SizedBox(height: AppSpacing.sm),
          ],
          if (edit.explanation != null) ...[
            Text(
              edit.explanation!,
              style: const TextStyle(color: AppColors.textSecondary),
            ),
            const SizedBox(height: AppSpacing.md),
          ],
          if (adaptiveOperations.isNotEmpty) ...[
            _AdaptiveDetails(
              edit: edit,
              metadataCatalog: metadataCatalog,
              operations: adaptiveOperations,
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
                      metadataCatalog.metadataFor(entry.key)!.label,
                      style: const TextStyle(color: AppColors.textSecondary),
                    ),
                  ),
                  Text(
                    metadataCatalog
                        .metadataFor(entry.key)!
                        .formatValue((entry.value as num).toDouble()),
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

class _StyleDetails extends StatelessWidget {
  const _StyleDetails({required this.style});

  final StyleMetadata style;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      key: const Key('active_style_details'),
      decoration: BoxDecoration(
        color: AppColors.accentSoft,
        borderRadius: BorderRadius.circular(AppRadii.small),
        border: Border.all(color: AppColors.accent.withValues(alpha: 0.35)),
      ),
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.sm),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                const Icon(
                  Icons.palette_outlined,
                  size: 18,
                  color: AppColors.accentBright,
                ),
                const SizedBox(width: AppSpacing.xs),
                Expanded(
                  child: Text(
                    style.displayName,
                    style: const TextStyle(fontWeight: FontWeight.w700),
                  ),
                ),
                Text(
                  '${(style.strength * 100).round()}%',
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
              ],
            ),
            const SizedBox(height: 4),
            Text(
              '${styleFamilyLabel(style.family)} · '
              '${style.styleId}@${style.version}',
              style: const TextStyle(
                color: AppColors.textSecondary,
                fontSize: 12,
              ),
            ),
            const SizedBox(height: 2),
            Text(
              '理解結果：已套用 ${style.displayName}，'
              'renderer ${style.rendererVersion}',
              style: const TextStyle(color: AppColors.textMuted, fontSize: 11),
            ),
          ],
        ),
      ),
    );
  }
}

class _AdaptiveDetails extends StatelessWidget {
  const _AdaptiveDetails({
    required this.edit,
    required this.metadataCatalog,
    required this.operations,
  });

  final EditHistoryItem edit;
  final ParameterMetadataCatalog metadataCatalog;
  final List<AdaptiveOperation> operations;

  @override
  Widget build(BuildContext context) {
    final policy = edit.adaptivePolicyVersion;
    final converged = operations.isNotEmpty
        ? operations.every((operation) => operation.converged == true)
        : edit.adaptiveConverged == true;
    final status = operations.length > 1
        ? '${operations.length} 項微調'
        : edit.adaptiveApplied == false
        ? '已重設區間'
        : converged
        ? '已收斂'
        : '持續微調';

    return Container(
      key: const Key('adaptive_details'),
      padding: const EdgeInsets.all(AppSpacing.sm),
      decoration: BoxDecoration(
        color: AppColors.accentSoft,
        borderRadius: BorderRadius.circular(AppRadii.small),
        border: Border.all(color: AppColors.accent.withValues(alpha: 0.35)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              const Icon(Icons.tune, size: 18, color: AppColors.accentBright),
              const SizedBox(width: AppSpacing.xs),
              const Expanded(
                child: Text(
                  '自適應微調',
                  style: TextStyle(fontWeight: FontWeight.w700),
                ),
              ),
              Text(
                status,
                key: const Key('adaptive_converged'),
                style: TextStyle(
                  color: converged == true
                      ? AppColors.success
                      : AppColors.textSecondary,
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
          ),
          if (policy != null || edit.adaptiveSchemaVersion != null) ...[
            const SizedBox(height: AppSpacing.xs),
            Wrap(
              spacing: AppSpacing.xs,
              runSpacing: 4,
              children: [
                if (policy != null)
                  _AdaptiveTag(key: const Key('adaptive_policy'), text: policy),
                if (edit.adaptiveSchemaVersion != null)
                  _AdaptiveTag(
                    key: const Key('adaptive_schema'),
                    text: edit.adaptiveSchemaVersion!,
                  ),
              ],
            ),
          ],
          for (var index = 0; index < operations.length; index++) ...[
            const SizedBox(height: AppSpacing.xs),
            _AdaptiveOperationDetails(
              operation: operations[index],
              metadata: metadataCatalog.metadataFor(operations[index].axis)!,
              index: index,
              singleOperation: operations.length == 1,
            ),
          ],
          if (operations.isEmpty && edit.adaptiveReason != null) ...[
            const SizedBox(height: AppSpacing.xs),
            _AdaptiveTag(
              key: const Key('adaptive_reason'),
              text: _adaptiveReasonLabel(edit.adaptiveReason!),
            ),
          ],
        ],
      ),
    );
  }
}

class _AdaptiveOperationDetails extends StatelessWidget {
  const _AdaptiveOperationDetails({
    required this.operation,
    required this.metadata,
    required this.index,
    required this.singleOperation,
  });

  final AdaptiveOperation operation;
  final ParameterMetadata metadata;
  final int index;
  final bool singleOperation;

  Key _key(String name) =>
      Key(singleOperation ? 'adaptive_$name' : 'adaptive_${name}_${index + 1}');

  @override
  Widget build(BuildContext context) {
    final axis = operation.axis;
    return DecoratedBox(
      key: Key('adaptive_operation_${index + 1}_$axis'),
      decoration: BoxDecoration(
        color: AppColors.surface.withValues(alpha: 0.55),
        borderRadius: BorderRadius.circular(AppRadii.small),
        border: Border.all(color: AppColors.border),
      ),
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.xs),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Wrap(
              spacing: AppSpacing.xs,
              runSpacing: 4,
              children: [
                _AdaptiveTag(key: _key('axis'), text: metadata.label),
                _AdaptiveTag(
                  key: _key('region'),
                  text: regionLabel(operation.region),
                ),
                if (operation.reason != null)
                  _AdaptiveTag(
                    key: _key('reason'),
                    text: _adaptiveReasonLabel(operation.reason!),
                  ),
              ],
            ),
            if (operation.deltaFromParent != null)
              _AdaptiveValueRow(
                key: _key('delta'),
                label: '本次相對調整',
                value: metadata.formatValue(
                  operation.deltaFromParent!,
                  signed: true,
                ),
              ),
            if (operation.currentValue != null || operation.nextValue != null)
              _AdaptiveValueRow(
                key: _key('current_next'),
                label: '候選值',
                value:
                    '${_formatAdaptiveOptional(metadata, operation.currentValue)} → '
                    '${_formatAdaptiveOptional(metadata, operation.nextValue)}',
              ),
            if (operation.lowerBound != null || operation.upperBound != null)
              _AdaptiveValueRow(
                key: _key('bounds'),
                label: '目前界線',
                value:
                    '${operation.lowerBound == null ? '−∞' : metadata.formatValue(operation.lowerBound!)} ～ '
                    '${operation.upperBound == null ? '+∞' : metadata.formatValue(operation.upperBound!)}',
              ),
            if (operation.stepBefore != null || operation.stepAfter != null)
              _AdaptiveValueRow(
                key: _key('steps'),
                label: metadata.usesLogTransform
                    ? '步幅（${metadata.transform}）'
                    : '步幅',
                value:
                    '${_formatAdaptiveStep(metadata, operation.stepBefore)} → '
                    '${_formatAdaptiveStep(metadata, operation.stepAfter)}',
              ),
          ],
        ),
      ),
    );
  }
}

class _AdaptiveTag extends StatelessWidget {
  const _AdaptiveTag({super.key, required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        color: AppColors.surface.withValues(alpha: 0.72),
        borderRadius: BorderRadius.circular(AppRadii.small),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 4),
        child: Text(
          text,
          style: const TextStyle(color: AppColors.textSecondary, fontSize: 11),
        ),
      ),
    );
  }
}

class _AdaptiveValueRow extends StatelessWidget {
  const _AdaptiveValueRow({
    super.key,
    required this.label,
    required this.value,
  });

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 7),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: Text(
              label,
              style: const TextStyle(
                color: AppColors.textSecondary,
                fontSize: 12,
              ),
            ),
          ),
          const SizedBox(width: AppSpacing.xs),
          Flexible(
            flex: 2,
            child: Text(
              value,
              textAlign: TextAlign.end,
              softWrap: true,
              style: const TextStyle(
                fontWeight: FontWeight.w600,
                fontSize: 12,
                fontFeatures: [FontFeature.tabularFigures()],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

String _adaptiveReasonLabel(String reason) {
  switch (reason) {
    case 'initial_step':
    case 'initial_anchor_step':
    case 'initial_template':
      return '建立初始步幅';
    case 'initial_negative_bracket':
      return '從目前效果往回收斂';
    case 'companion_takeover':
      return '接手相關參數調整';
    case 'bracket_midpoint':
    case 'bounded_midpoint':
      return '依回饋取區間中點';
    case 'unbounded_same_direction':
    case 'same_direction_unbounded':
    case 'unbounded_template_step':
      return '延續同方向探索';
    case 'direction_reversal':
    case 'reverse_direction':
      return '依反向回饋縮小';
    case 'state_reset':
    case 'explicit_strength_reset':
      return '重新建立調整基準';
    case 'absolute_value_reset':
      return '採用明確數值並重設區間';
    case 'relative_numeric_reset':
      return '依相對數值調整';
    case 'axis_reset':
      return '重設單一參數';
    case 'global_reset':
      return '回到原圖';
    default:
      return reason;
  }
}

String _formatAdaptiveOptional(ParameterMetadata metadata, double? value) {
  return value == null ? '—' : metadata.formatValue(value);
}

String _formatAdaptiveStep(ParameterMetadata metadata, double? value) {
  if (value == null) {
    return '—';
  }
  return metadata.formatStep(value);
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
    this.messageIsError = true,
  });

  final String title;
  final String? subtitle;
  final IconData icon;
  final VoidCallback onClose;
  final Widget child;
  final String? message;
  final bool messageIsError;

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
        if (message != null)
          PanelMessage(message: message!, isError: messageIsError),
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
    required this.metadataCatalog,
    required this.version,
    required this.parentVersion,
    required this.selected,
    required this.onTap,
  });

  final EditHistoryItem item;
  final ParameterMetadataCatalog metadataCatalog;
  final int version;
  final int? parentVersion;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final summary = compactParameterSummary(
      item.parametersForDisplay(metadataCatalog),
      metadataCatalog: metadataCatalog,
    );
    final parameterSummary = item.isDirectStyleEdit && summary.isNotEmpty
        ? '等效 $summary'
        : summary;
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
                    Align(
                      alignment: Alignment.centerLeft,
                      child: _HistoryBranchBadge(
                        key: Key('history_branch_badge_${item.editId}'),
                        isRoot: item.parentEditId == null,
                        parentVersion: parentVersion,
                      ),
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
                      '${item.targetLabel}'
                      '${parameterSummary.isEmpty ? '' : ' · $parameterSummary'}',
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

class _HistoryBranchBadge extends StatelessWidget {
  const _HistoryBranchBadge({
    super.key,
    required this.isRoot,
    required this.parentVersion,
  });

  final bool isRoot;
  final int? parentVersion;

  @override
  Widget build(BuildContext context) {
    final label = isRoot
        ? '根分支'
        : parentVersion == null
        ? '接續父版本'
        : '接續版本 $parentVersion';
    return DecoratedBox(
      decoration: BoxDecoration(
        color: AppColors.surfaceSoft,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
        child: Text(
          label,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(color: AppColors.textMuted, fontSize: 10),
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
