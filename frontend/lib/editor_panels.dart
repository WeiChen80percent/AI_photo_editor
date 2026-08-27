import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';

import 'app_theme.dart';
import 'editor_localizations.dart';
import 'edit_models.dart';
import 'editor_controller.dart';
import 'l10n/l10n_context.dart';
import 'speech_models.dart';

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
    final l10n = context.l10n;
    final plan = controller.commandPlan;
    return PanelScaffold(
      title: l10n.promptEditTitle,
      subtitle: controller.selectedEdit == null
          ? controller.isOriginalBaseSelected && controller.history.isNotEmpty
                ? l10n.promptBranchFromOriginal
                : l10n.promptFirstVersionFromOriginal
          : l10n.promptContinueSelected,
      icon: Icons.auto_awesome_outlined,
      onClose: onClose,
      message: _localizedControllerMessage(context, controller),
      messageIsError: _hasControllerError(controller),
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
            onSubmitted: (_) {
              if (controller.canSubmitPrompt) {
                onSubmit();
              }
            },
            decoration: InputDecoration(hintText: l10n.promptHint),
          ),
          const SizedBox(height: AppSpacing.sm),
          _SpeechControls(controller: controller),
          const SizedBox(height: AppSpacing.sm),
          Text(
            l10n.promptModeNotice,
            style: TextStyle(
              color: context.editorColors.textMuted,
              fontSize: 12,
            ),
          ),
          if (plan != null) ...[
            const SizedBox(height: AppSpacing.sm),
            _CommandPlanCard(
              controller: controller,
              plan: plan,
              onCompleted: onClose,
            ),
          ],
          const SizedBox(height: AppSpacing.lg),
          FilledButton.icon(
            key: const Key('submit_prompt_button'),
            onPressed: controller.canSubmitPrompt ? onSubmit : null,
            icon: controller.isProcessing || controller.isPlanningCommand
                ? SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(
                      strokeWidth: 2,
                      color: Theme.of(context).colorScheme.onPrimary,
                    ),
                  )
                : const Icon(Icons.auto_fix_high),
            label: Text(
              controller.isPlanningCommand
                  ? l10n.commandPlanning
                  : controller.isProcessing
                  ? l10n.processing
                  : l10n.applyPrompt,
            ),
          ),
        ],
      ),
    );
  }
}

class _CommandPlanCard extends StatelessWidget {
  const _CommandPlanCard({
    required this.controller,
    required this.plan,
    required this.onCompleted,
  });

  final EditorController controller;
  final CommandPlan plan;
  final VoidCallback onCompleted;

  @override
  Widget build(BuildContext context) {
    final languageCode = Localizations.localeOf(context).languageCode;
    final colors = context.editorColors;
    final clarification = plan.clarification;
    return DecoratedBox(
      key: const Key('command_plan_card'),
      decoration: BoxDecoration(
        color: colors.surfaceSoft,
        borderRadius: BorderRadius.circular(AppRadii.small),
        border: Border.all(color: colors.border),
      ),
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.sm),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                const Icon(Icons.account_tree_outlined, size: 18),
                const SizedBox(width: AppSpacing.xs),
                Expanded(
                  child: Text(
                    context.l10n.commandPlanTitle,
                    style: const TextStyle(fontWeight: FontWeight.w700),
                  ),
                ),
              ],
            ),
            const SizedBox(height: AppSpacing.xs),
            Text(
              plan.summary.forLanguage(languageCode),
              style: TextStyle(color: colors.textSecondary, fontSize: 12),
            ),
            if (clarification != null) ...[
              const SizedBox(height: AppSpacing.sm),
              Text(
                clarification.question.forLanguage(languageCode),
                key: const Key('command_clarification_question'),
                style: const TextStyle(fontWeight: FontWeight.w600),
              ),
              if (clarification.options.isNotEmpty) ...[
                const SizedBox(height: AppSpacing.xs),
                Wrap(
                  spacing: AppSpacing.xs,
                  runSpacing: AppSpacing.xs,
                  children: [
                    for (final option in clarification.options)
                      OutlinedButton(
                        key: Key('command_option_${option.optionId}'),
                        onPressed: controller.isPlanningCommand
                            ? null
                            : () async {
                                final completed = await controller
                                    .chooseCommandClarificationOption(option);
                                if (completed) {
                                  onCompleted();
                                }
                              },
                        child: Text(option.label.forLanguage(languageCode)),
                      ),
                  ],
                ),
              ],
            ],
            if (plan.isPhotoGit && controller.photoGitPlan != null) ...[
              const SizedBox(height: AppSpacing.sm),
              Text(
                context.l10n.commandPreviewNotice,
                style: TextStyle(color: colors.textMuted, fontSize: 12),
              ),
              const SizedBox(height: AppSpacing.xs),
              _PhotoGitPlanCard(
                controller: controller,
                versionLabel: (editId) =>
                    _promptVersionLabel(context, controller, editId),
              ),
              const SizedBox(height: AppSpacing.sm),
              Wrap(
                spacing: AppSpacing.xs,
                runSpacing: AppSpacing.xs,
                children: [
                  if (controller.photoGitPreview == null)
                    FilledButton.tonalIcon(
                      key: const Key('command_photo_git_preview'),
                      onPressed: controller.canPreviewPhotoGit
                          ? () => unawaited(controller.previewPhotoGit())
                          : null,
                      icon: controller.isPreviewingPhotoGit
                          ? const SizedBox(
                              width: 16,
                              height: 16,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : const Icon(Icons.preview_outlined),
                      label: Text(context.l10n.photoGitPreview),
                    ),
                  if (controller.photoGitPreview != null)
                    FilledButton.icon(
                      key: const Key('command_photo_git_commit'),
                      onPressed: controller.canCommitPhotoGit
                          ? () async {
                              final completed = await controller
                                  .commitPhotoGit();
                              if (completed) {
                                onCompleted();
                              }
                            }
                          : null,
                      icon: controller.isCommittingPhotoGit
                          ? const SizedBox(
                              width: 16,
                              height: 16,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : const Icon(Icons.call_merge),
                      label: Text(context.l10n.photoGitCommit),
                    ),
                  TextButton(
                    key: const Key('command_photo_git_cancel'),
                    onPressed:
                        controller.isPreviewingPhotoGit ||
                            controller.isCommittingPhotoGit
                        ? null
                        : controller.discardPhotoGitDraft,
                    child: Text(context.l10n.photoGitCancel),
                  ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }
}

String _promptVersionLabel(
  BuildContext context,
  EditorController controller,
  String? editId,
) {
  if (editId == null || editId == EditorController.originalParentSentinel) {
    return context.l10n.labelOriginal;
  }
  final index = controller.history.indexWhere((edit) => edit.editId == editId);
  if (index < 0) {
    return editId;
  }
  return 'v${index + 1} · '
      '${localizedEditDisplayTitle(context.l10n, controller.history[index])}';
}

class _SpeechControls extends StatelessWidget {
  const _SpeechControls({required this.controller});

  final EditorController controller;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final colors = context.editorColors;
    if (!controller.hasSpeechInput) {
      return Text(
        l10n.speechUnavailable,
        key: const Key('speech_unavailable'),
        style: TextStyle(color: colors.textMuted, fontSize: 12),
      );
    }

    switch (controller.speechInputState) {
      case SpeechInputState.requestingPermission:
        return _SpeechProgress(
          key: const Key('speech_requesting_permission'),
          label: l10n.speechRequestingPermission,
        );
      case SpeechInputState.recording:
        return Wrap(
          key: const Key('speech_recording_controls'),
          spacing: AppSpacing.sm,
          runSpacing: AppSpacing.xs,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(Icons.fiber_manual_record, size: 14, color: colors.error),
                const SizedBox(width: AppSpacing.xxs),
                Text(
                  l10n.speechRecordingSeconds(
                    controller.speechRecordingElapsedSeconds,
                  ),
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
              ],
            ),
            FilledButton.tonalIcon(
              key: const Key('stop_speech_recording'),
              onPressed: () => unawaited(controller.stopSpeechRecording()),
              icon: const Icon(Icons.stop_rounded),
              label: Text(l10n.speechStop),
            ),
            TextButton.icon(
              key: const Key('cancel_speech_recording'),
              onPressed: () => unawaited(controller.cancelSpeechRecording()),
              icon: const Icon(Icons.close),
              label: Text(l10n.speechCancel),
            ),
          ],
        );
      case SpeechInputState.transcribing:
        return _SpeechProgress(
          key: const Key('speech_transcribing'),
          label: l10n.speechTranscribing,
        );
      case SpeechInputState.idle:
      case SpeechInputState.completed:
      case SpeechInputState.cancelled:
      case SpeechInputState.error:
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            DropdownButtonFormField<SpeechLanguageMode>(
              key: ValueKey<String>(
                'speech_language_${controller.speechLanguageMode.name}',
              ),
              initialValue: controller.speechLanguageMode,
              isExpanded: true,
              decoration: InputDecoration(
                labelText: l10n.speechLanguageLabel,
                helperText: l10n.speechLanguageHelp,
              ),
              items: [
                DropdownMenuItem<SpeechLanguageMode>(
                  value: SpeechLanguageMode.traditionalChinese,
                  child: Text(l10n.speechLanguageTraditionalChinese),
                ),
                DropdownMenuItem<SpeechLanguageMode>(
                  value: SpeechLanguageMode.english,
                  child: Text(l10n.speechLanguageEnglish),
                ),
                DropdownMenuItem<SpeechLanguageMode>(
                  value: SpeechLanguageMode.automatic,
                  child: Text(l10n.speechLanguageAutomatic),
                ),
              ],
              onChanged: controller.isProcessing
                  ? null
                  : (mode) {
                      if (mode != null) {
                        controller.setSpeechLanguageMode(mode);
                      }
                    },
            ),
            const SizedBox(height: AppSpacing.sm),
            OutlinedButton.icon(
              key: const Key('start_speech_recording'),
              onPressed: controller.canStartSpeechRecording
                  ? () => unawaited(controller.startSpeechRecording())
                  : null,
              icon: const Icon(Icons.mic_none_outlined),
              label: Text(l10n.speechStart),
            ),
            const SizedBox(height: AppSpacing.xxs),
            Text(
              l10n.speechPrivacyNotice,
              style: TextStyle(color: colors.textMuted, fontSize: 12),
            ),
            if (controller.lastSpeechTranscription case final result?) ...[
              const SizedBox(height: AppSpacing.xxs),
              Text(
                l10n.speechResultMetadata(
                  _speechLanguageLabel(context, result.language),
                  result.modelId.split('/').last,
                ),
                key: const Key('speech_result_metadata'),
                style: TextStyle(color: colors.textMuted, fontSize: 12),
              ),
            ],
          ],
        );
    }
  }
}

String _speechLanguageLabel(BuildContext context, String language) {
  final normalized = language.trim().toLowerCase();
  return switch (normalized) {
    'zh' => context.l10n.speechLanguageTraditionalChinese,
    'en' => context.l10n.speechLanguageEnglish,
    '' || 'auto' => context.l10n.speechLanguageAutomatic,
    _ => normalized.toUpperCase(),
  };
}

class _SpeechProgress extends StatelessWidget {
  const _SpeechProgress({super.key, required this.label});

  final String label;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        const SizedBox(
          width: 18,
          height: 18,
          child: CircularProgressIndicator(strokeWidth: 2),
        ),
        const SizedBox(width: AppSpacing.sm),
        Expanded(child: Text(label)),
      ],
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
    final l10n = context.l10n;
    final catalog = controller.styleCatalog;
    if (controller.isLoadingStyles) {
      return PanelScaffold(
        title: l10n.styleCatalogTitle,
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
        title: l10n.styleCatalogTitle,
        icon: Icons.palette_outlined,
        onClose: onClose,
        message: _localizedControllerError(context, controller),
        messageIsError: true,
        child: _UnavailablePanel(
          icon: Icons.palette_outlined,
          message: l10n.styleCatalogUnavailable,
        ),
      );
    }

    final styles = controller.visibleStyles;
    final families = catalog.families.keys.toList()..sort();
    return Column(
      key: const Key('styles_panel'),
      children: [
        PanelHeader(
          title: l10n.styleCatalogTitle,
          subtitle: l10n.styleCatalogSubtitle(
            catalog.styleCount,
            catalog.catalogVersion,
          ),
          icon: Icons.palette_outlined,
          onClose: onClose,
        ),
        if (_hasControllerError(controller))
          PanelMessage(
            message: _localizedControllerError(context, controller)!,
            isError: true,
          ),
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
              tooltip: context.l10n.styleCategoryPrevious,
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
                          label: Text(context.l10n.styleCategoryAll),
                          selected: widget.selectedFamily == null,
                          onSelected: (_) => widget.onSelected(null),
                        ),
                        for (final family in widget.families) ...[
                          const SizedBox(width: AppSpacing.xs),
                          ChoiceChip(
                            key: Key('style_family_$family'),
                            label: Text(
                              '${localizedStyleFamilyLabel(context.l10n, family)} '
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
              tooltip: context.l10n.styleCategoryNext,
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
    final colors = context.editorColors;
    final styleName = localizedCatalogStyleName(context.l10n, style);
    final secondaryLabel = _localizedCatalogStyleSecondaryLabel(context, style);
    return DecoratedBox(
      decoration: BoxDecoration(
        color: colors.surfaceRaised,
        borderRadius: BorderRadius.circular(AppRadii.medium),
        border: Border.all(color: colors.border),
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
                    ? ColoredBox(
                        color: colors.surfaceSoft,
                        child: Icon(
                          Icons.palette_outlined,
                          color: colors.textMuted,
                        ),
                      )
                    : Image.network(
                        style.previewUrl!,
                        fit: BoxFit.cover,
                        errorBuilder: (_, _, _) => ColoredBox(
                          color: colors.surfaceSoft,
                          child: Icon(
                            Icons.broken_image_outlined,
                            color: colors.textMuted,
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
                          styleName,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(fontWeight: FontWeight.w700),
                        ),
                      ),
                      Text(
                        'v${style.version}',
                        style: TextStyle(color: colors.textMuted, fontSize: 11),
                      ),
                    ],
                  ),
                  const SizedBox(height: 2),
                  Text(
                    secondaryLabel,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(color: colors.textSecondary, fontSize: 11),
                  ),
                  const SizedBox(height: AppSpacing.xs),
                  Row(
                    children: [
                      Text(
                        context.l10n.styleStrength,
                        style: TextStyle(color: colors.textMuted, fontSize: 11),
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
                      tooltip: context.l10n.applyStyle,
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
    final l10n = context.l10n;
    return PanelScaffold(
      title: l10n.referenceEditTitle,
      subtitle: controller.selectedEdit == null
          ? l10n.referenceFromOriginal
          : l10n.referenceFromCurrent,
      icon: Icons.photo_outlined,
      onClose: onClose,
      message: _localizedControllerError(context, controller),
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
                    controller.referenceImageBytes == null
                        ? l10n.selectReference
                        : l10n.changeReference,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ),
              if (controller.referenceImageBytes != null) ...[
                const SizedBox(width: AppSpacing.xs),
                IconButton.outlined(
                  key: const Key('clear_reference_button'),
                  tooltip: l10n.removeReference,
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
                ? SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(
                      strokeWidth: 2,
                      color: Theme.of(context).colorScheme.onPrimary,
                    ),
                  )
                : const Icon(Icons.auto_fix_high),
            label: Text(
              controller.isProcessing ? l10n.processing : l10n.applyReference,
            ),
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
    final l10n = context.l10n;
    if (controller.isLoadingManual) {
      return PanelScaffold(
        title: l10n.manualEditTitle,
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
        title: l10n.manualEditTitle,
        icon: Icons.tune,
        onClose: onClose,
        message: _localizedControllerError(context, controller),
        child: _UnavailablePanel(
          icon: Icons.tune,
          message: _localizedManualDisabledReason(context, controller),
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
          title: l10n.manualEditTitle,
          subtitle: source == null
              ? null
              : l10n.manualSourceVersion(
                  localizedRegionLabel(context.l10n, source.region),
                  localizedEditModeLabel(context.l10n, source),
                ),
          icon: Icons.tune,
          onClose: onClose,
          trailing: controller.manualIsDirty ? const _DraftStatus() : null,
        ),
        if (_hasControllerError(controller))
          PanelMessage(
            message: _localizedControllerError(context, controller)!,
            isError: true,
          ),
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
                        Expanded(
                          child: Text(
                            l10n.advancedAdjustments,
                            style: const TextStyle(fontWeight: FontWeight.w600),
                          ),
                        ),
                        Icon(
                          controller.manualAdvancedExpanded
                              ? Icons.expand_less
                              : Icons.expand_more,
                          color: context.editorColors.textSecondary,
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
    final l10n = context.l10n;
    final items = controller.history.reversed.toList();
    return Column(
      key: const Key('history_panel'),
      children: [
        PanelHeader(
          title: l10n.historyTitle,
          subtitle: l10n.historyVersionCount(controller.history.length),
          icon: Icons.history,
          onClose: onClose,
          trailing: IconButton(
            key: const Key('refresh_history_button'),
            tooltip: l10n.refreshHistory,
            onPressed: controller.sessionId == null
                ? null
                : () => controller.refreshHistory(),
            icon: const Icon(Icons.refresh, size: 20),
          ),
        ),
        if (_hasControllerError(controller))
          PanelMessage(
            message: _localizedControllerError(context, controller)!,
            isError: true,
          ),
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
              _PhotoGitPanel(controller: controller),
              if (items.isNotEmpty) ...[
                const SizedBox(height: AppSpacing.sm),
                OutlinedButton.icon(
                  key: const Key('history_original_branch'),
                  onPressed: onSelectOriginal,
                  icon: const Icon(Icons.account_tree_outlined, size: 18),
                  label: Text(
                    controller.isOriginalBaseSelected
                        ? l10n.selectedOriginalNewBranch
                        : l10n.createBranchFromOriginal,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                const SizedBox(height: AppSpacing.sm),
              ],
              if (items.isEmpty)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: AppSpacing.xl),
                  child: _UnavailablePanel(
                    icon: Icons.history,
                    message: l10n.emptyHistory,
                  ),
                )
              else
                for (var index = 0; index < items.length; index++) ...[
                  if (index > 0) const SizedBox(height: AppSpacing.xs),
                  _buildHistoryTile(items[index]),
                ],
            ],
          ),
        ),
      ],
    );
  }

  Widget _buildHistoryTile(EditHistoryItem item) {
    final originalIndex = controller.history.indexWhere(
      (entry) => entry.editId == item.editId,
    );
    final parentIndex = item.parentEditId == null
        ? -1
        : controller.history.indexWhere(
            (entry) => entry.editId == item.parentEditId,
          );
    final sourceIds = item.photoGit?.sourceEditIds;
    final sourceId = sourceIds == null || sourceIds.isEmpty
        ? null
        : sourceIds.first;
    final sourceIndex = sourceId == null
        ? -1
        : controller.history.indexWhere((entry) => entry.editId == sourceId);
    final revertedId = item.photoGit?.revertedEditId;
    final revertedIndex = revertedId == null
        ? -1
        : controller.history.indexWhere((entry) => entry.editId == revertedId);
    return HistoryTile(
      key: Key('history_${item.editId}'),
      item: item,
      metadataCatalog: controller.metadataCatalogFor(item),
      version: originalIndex + 1,
      parentVersion: parentIndex < 0 ? null : parentIndex + 1,
      sourceVersion: sourceIndex < 0 ? null : sourceIndex + 1,
      revertedVersion: revertedIndex < 0 ? null : revertedIndex + 1,
      selected: item.editId == controller.selectedEditId,
      onTap: () => onSelect(item),
    );
  }
}

class _PhotoGitPanel extends StatefulWidget {
  const _PhotoGitPanel({required this.controller});

  final EditorController controller;

  @override
  State<_PhotoGitPanel> createState() => _PhotoGitPanelState();
}

class _PhotoGitPanelState extends State<_PhotoGitPanel> {
  late final TextEditingController _instructionController;

  static const _regions = <String>[
    'all',
    'sky',
    'person',
    'background',
    'highlights',
    'shadows',
  ];

  static const _parameters = <String>[
    'brightness',
    'contrast',
    'saturation',
    'temperature',
    'clarity',
    'dehaze',
  ];

  @override
  void initState() {
    super.initState();
    _instructionController = TextEditingController(
      text: widget.controller.photoGitInstruction,
    );
  }

  @override
  void didUpdateWidget(covariant _PhotoGitPanel oldWidget) {
    super.didUpdateWidget(oldWidget);
    final value = widget.controller.photoGitInstruction;
    if (_instructionController.text != value) {
      _instructionController.value = TextEditingValue(
        text: value,
        selection: TextSelection.collapsed(offset: value.length),
      );
    }
  }

  @override
  void dispose() {
    _instructionController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final controller = widget.controller;
    final l10n = context.l10n;
    final target = controller.photoGitTargetEdit;
    final enabled =
        target != null &&
        target.engine.toLowerCase() == 'opencv' &&
        controller.sessionId != null &&
        !controller.manualIsDirty;
    return Card(
      key: const Key('photo_git_panel'),
      margin: EdgeInsets.zero,
      clipBehavior: Clip.antiAlias,
      child: ExpansionTile(
        key: const Key('photo_git_expansion'),
        initiallyExpanded: controller.hasPhotoGitDraft,
        leading: const Icon(Icons.merge_type),
        title: Text(
          l10n.photoGitTitle,
          style: const TextStyle(fontWeight: FontWeight.w700),
        ),
        subtitle: Text(l10n.photoGitSubtitle),
        childrenPadding: const EdgeInsets.fromLTRB(
          AppSpacing.sm,
          0,
          AppSpacing.sm,
          AppSpacing.sm,
        ),
        children: [
          if (!enabled)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
              child: Text(
                controller.manualIsDirty
                    ? l10n.photoGitManualDraftBlocked
                    : l10n.photoGitUnavailable,
                style: TextStyle(
                  color: context.editorColors.textMuted,
                  fontSize: 12,
                ),
              ),
            )
          else ...[
            _TargetVersionRow(
              label: l10n.photoGitTarget,
              value: _versionLabel(context, target),
              imageUrl: target.resultUrl,
            ),
            const SizedBox(height: AppSpacing.sm),
            SegmentedButton<PhotoGitOperation>(
              key: const Key('photo_git_operation'),
              segments: <ButtonSegment<PhotoGitOperation>>[
                ButtonSegment<PhotoGitOperation>(
                  value: PhotoGitOperation.merge,
                  icon: const Icon(Icons.call_merge, size: 18),
                  label: Text(l10n.photoGitMerge),
                ),
                ButtonSegment<PhotoGitOperation>(
                  value: PhotoGitOperation.selectiveRevert,
                  icon: const Icon(Icons.undo, size: 18),
                  label: Text(l10n.photoGitSelectiveRevert),
                ),
              ],
              selected: <PhotoGitOperation>{controller.photoGitOperation},
              onSelectionChanged: controller.isCommittingPhotoGit
                  ? null
                  : (selection) {
                      controller.setPhotoGitOperation(selection.first);
                    },
              showSelectedIcon: false,
              style: const ButtonStyle(visualDensity: VisualDensity.compact),
            ),
            const SizedBox(height: AppSpacing.sm),
            if (controller.photoGitOperation == PhotoGitOperation.merge)
              _buildSourcePicker(context)
            else
              _buildRevertPicker(context),
            const SizedBox(height: AppSpacing.sm),
            TextField(
              key: const Key('photo_git_instruction'),
              controller: _instructionController,
              minLines: 2,
              maxLines: 3,
              enabled: !controller.isCommittingPhotoGit,
              onChanged: controller.setPhotoGitInstruction,
              decoration: InputDecoration(
                labelText: l10n.photoGitInstruction,
                hintText:
                    controller.photoGitOperation == PhotoGitOperation.merge
                    ? l10n.photoGitMergeHint
                    : l10n.photoGitRevertHint,
              ),
            ),
            const SizedBox(height: AppSpacing.sm),
            Align(
              alignment: Alignment.centerLeft,
              child: Text(
                l10n.photoGitScopeAssist,
                style: TextStyle(
                  color: context.editorColors.textSecondary,
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ),
            const SizedBox(height: AppSpacing.xs),
            _buildScopeChips(context),
            const SizedBox(height: AppSpacing.sm),
            SizedBox(
              width: double.infinity,
              child: FilledButton.icon(
                key: const Key('photo_git_analyze'),
                onPressed: controller.canPlanPhotoGit
                    ? controller.analyzePhotoGit
                    : null,
                icon: controller.isPlanningPhotoGit
                    ? const SizedBox.square(
                        dimension: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.manage_search),
                label: Text(
                  controller.isPlanningPhotoGit
                      ? l10n.photoGitAnalyzing
                      : l10n.photoGitAnalyze,
                ),
              ),
            ),
            if (controller.photoGitPlan != null) ...[
              const SizedBox(height: AppSpacing.sm),
              _PhotoGitPlanCard(
                controller: controller,
                versionLabel: (editId) => _versionLabelForId(context, editId),
              ),
            ],
            if (controller.photoGitPlan != null) ...[
              const SizedBox(height: AppSpacing.sm),
              Wrap(
                spacing: AppSpacing.xs,
                runSpacing: AppSpacing.xs,
                children: [
                  OutlinedButton.icon(
                    key: const Key('photo_git_preview'),
                    onPressed: controller.canPreviewPhotoGit
                        ? controller.previewPhotoGit
                        : null,
                    icon: controller.isPreviewingPhotoGit
                        ? const SizedBox.square(
                            dimension: 16,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.preview_outlined),
                    label: Text(
                      controller.isPreviewingPhotoGit
                          ? l10n.photoGitPreviewing
                          : l10n.photoGitPreview,
                    ),
                  ),
                  FilledButton.icon(
                    key: const Key('photo_git_commit'),
                    onPressed: controller.canCommitPhotoGit
                        ? controller.commitPhotoGit
                        : null,
                    icon: controller.isCommittingPhotoGit
                        ? const SizedBox.square(
                            dimension: 16,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.add_task),
                    label: Text(
                      controller.isCommittingPhotoGit
                          ? l10n.photoGitCommitting
                          : l10n.photoGitCommit,
                    ),
                  ),
                ],
              ),
            ],
            if (controller.hasPhotoGitDraft) ...[
              const SizedBox(height: AppSpacing.xs),
              Align(
                alignment: Alignment.centerLeft,
                child: TextButton.icon(
                  key: const Key('photo_git_cancel'),
                  onPressed: controller.isCommittingPhotoGit
                      ? null
                      : controller.discardPhotoGitDraft,
                  icon: const Icon(Icons.close, size: 18),
                  label: Text(l10n.photoGitCancel),
                ),
              ),
            ],
          ],
        ],
      ),
    );
  }

  Widget _buildSourcePicker(BuildContext context) {
    final controller = widget.controller;
    final candidates = controller.photoGitSourceCandidates;
    return DropdownButtonFormField<String>(
      key: ValueKey<String>(
        'photo_git_source_${controller.photoGitSourceEditId}',
      ),
      initialValue:
          candidates.any(
            (edit) => edit.editId == controller.photoGitSourceEditId,
          )
          ? controller.photoGitSourceEditId
          : null,
      isExpanded: true,
      decoration: InputDecoration(labelText: context.l10n.photoGitSource),
      hint: Text(context.l10n.photoGitChooseSource),
      items: [
        for (final edit in candidates)
          DropdownMenuItem<String>(
            value: edit.editId,
            child: Text(
              _versionLabel(context, edit),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ),
      ],
      onChanged: candidates.isEmpty || controller.isCommittingPhotoGit
          ? null
          : controller.setPhotoGitSource,
    );
  }

  Widget _buildRevertPicker(BuildContext context) {
    final controller = widget.controller;
    final candidates = controller.photoGitRevertCandidates;
    return DropdownButtonFormField<String>(
      key: ValueKey<String>(
        'photo_git_revert_${controller.photoGitRevertEditId}',
      ),
      initialValue:
          candidates.any(
            (edit) => edit.editId == controller.photoGitRevertEditId,
          )
          ? controller.photoGitRevertEditId
          : null,
      isExpanded: true,
      decoration: InputDecoration(labelText: context.l10n.photoGitRevertStep),
      hint: Text(context.l10n.photoGitChooseRevertStep),
      items: [
        for (final edit in candidates)
          DropdownMenuItem<String>(
            value: edit.editId,
            child: Text(
              _versionLabel(context, edit),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ),
      ],
      onChanged: candidates.isEmpty || controller.isCommittingPhotoGit
          ? null
          : controller.setPhotoGitRevertStep,
    );
  }

  Widget _buildScopeChips(BuildContext context) {
    final controller = widget.controller;
    final l10n = context.l10n;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Wrap(
          spacing: 5,
          runSpacing: 3,
          children: [
            ChoiceChip(
              key: const Key('photo_git_region_any'),
              label: Text(l10n.photoGitAnyRegion),
              selected: controller.photoGitRegion == null,
              onSelected: (_) => controller.setPhotoGitRegion(null),
            ),
            for (final region in _regions)
              ChoiceChip(
                key: Key('photo_git_region_$region'),
                label: Text(localizedRegionLabel(l10n, region)),
                selected: controller.photoGitRegion == region,
                onSelected: (selected) =>
                    controller.setPhotoGitRegion(selected ? region : null),
              ),
          ],
        ),
        const SizedBox(height: 4),
        Wrap(
          spacing: 5,
          runSpacing: 3,
          children: [
            ChoiceChip(
              key: const Key('photo_git_parameter_any'),
              label: Text(l10n.photoGitAnyParameter),
              selected: controller.photoGitParameter == null,
              onSelected: (_) => controller.setPhotoGitParameter(null),
            ),
            for (final parameter in _parameters)
              ChoiceChip(
                key: Key('photo_git_parameter_$parameter'),
                label: Text(localizedParameterLabel(l10n, parameter)),
                selected: controller.photoGitParameter == parameter,
                onSelected: (selected) => controller.setPhotoGitParameter(
                  selected ? parameter : null,
                ),
              ),
          ],
        ),
      ],
    );
  }

  String _versionLabel(BuildContext context, EditHistoryItem edit) {
    final index = widget.controller.history.indexWhere(
      (item) => item.editId == edit.editId,
    );
    final version = index < 0 ? '?' : '${index + 1}';
    return 'v$version · ${localizedEditDisplayTitle(context.l10n, edit)}';
  }

  String _versionLabelForId(BuildContext context, String? editId) {
    if (editId == null || editId == EditorController.originalParentSentinel) {
      return context.l10n.labelOriginal;
    }
    for (final edit in widget.controller.history) {
      if (edit.editId == editId) {
        return _versionLabel(context, edit);
      }
    }
    return editId;
  }
}

class _TargetVersionRow extends StatelessWidget {
  const _TargetVersionRow({
    required this.label,
    required this.value,
    required this.imageUrl,
  });

  final String label;
  final String value;
  final String imageUrl;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        ClipRRect(
          borderRadius: BorderRadius.circular(AppRadii.small),
          child: Image.network(
            imageUrl,
            width: 44,
            height: 44,
            fit: BoxFit.cover,
            errorBuilder: (_, _, _) => const SizedBox.square(
              dimension: 44,
              child: Icon(Icons.image_not_supported_outlined),
            ),
          ),
        ),
        const SizedBox(width: AppSpacing.sm),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                label,
                style: TextStyle(
                  color: context.editorColors.textMuted,
                  fontSize: 11,
                ),
              ),
              Text(
                value,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontWeight: FontWeight.w600),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _PhotoGitPlanCard extends StatelessWidget {
  const _PhotoGitPlanCard({
    required this.controller,
    required this.versionLabel,
  });

  final EditorController controller;
  final String Function(String? editId) versionLabel;

  @override
  Widget build(BuildContext context) {
    final plan = controller.photoGitPlan!;
    final l10n = context.l10n;
    final colors = context.editorColors;
    return DecoratedBox(
      key: const Key('photo_git_plan'),
      decoration: BoxDecoration(
        color: colors.surfaceSoft,
        borderRadius: BorderRadius.circular(AppRadii.small),
        border: Border.all(color: colors.border),
      ),
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.sm),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              l10n.photoGitPlanSummary,
              style: const TextStyle(fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: 4),
            Text(switch (plan.status) {
              'conflict' => l10n.statusPhotoGitConflictsFound,
              'no_change' => l10n.statusPhotoGitNoChange,
              _ => l10n.statusPhotoGitPlanReady,
            }, style: TextStyle(color: colors.textSecondary, fontSize: 12)),
            const SizedBox(height: AppSpacing.xs),
            Wrap(
              spacing: AppSpacing.xs,
              runSpacing: 4,
              children: [
                _PlanCountChip(
                  icon: Icons.add_circle_outline,
                  label:
                      '${l10n.photoGitAdded} ${plan.appliedContributions.length}',
                ),
                _PlanCountChip(
                  icon: Icons.remove_circle_outline,
                  label:
                      '${l10n.photoGitRemoved} ${plan.removedContributions.length}',
                ),
                _PlanCountChip(
                  icon: Icons.warning_amber_outlined,
                  label: '${l10n.photoGitConflicts} ${plan.conflicts.length}',
                ),
              ],
            ),
            if (plan.appliedContributions.isNotEmpty) ...[
              const SizedBox(height: AppSpacing.xs),
              _ContributionList(
                title: l10n.photoGitAdded,
                contributions: plan.appliedContributions,
              ),
            ],
            if (plan.removedContributions.isNotEmpty) ...[
              const SizedBox(height: AppSpacing.xs),
              _ContributionList(
                title: l10n.photoGitRemoved,
                contributions: plan.removedContributions,
              ),
            ],
            if (plan.conflicts.isNotEmpty) ...[
              const SizedBox(height: AppSpacing.sm),
              Text(
                l10n.photoGitConflictHelp,
                style: TextStyle(color: colors.textSecondary, fontSize: 12),
              ),
              const SizedBox(height: AppSpacing.xs),
              for (final conflict in plan.conflicts) ...[
                _PhotoGitConflictCard(
                  conflict: conflict,
                  controller: controller,
                  versionLabel: versionLabel,
                ),
                const SizedBox(height: AppSpacing.xs),
              ],
            ],
          ],
        ),
      ),
    );
  }
}

class _PlanCountChip extends StatelessWidget {
  const _PlanCountChip({required this.icon, required this.label});

  final IconData icon;
  final String label;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: context.editorColors.border),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 14),
            const SizedBox(width: 4),
            Text(label, style: const TextStyle(fontSize: 11)),
          ],
        ),
      ),
    );
  }
}

class _ContributionList extends StatelessWidget {
  const _ContributionList({required this.title, required this.contributions});

  final String title;
  final List<Map<String, dynamic>> contributions;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          title,
          style: TextStyle(
            color: context.editorColors.textMuted,
            fontSize: 11,
            fontWeight: FontWeight.w600,
          ),
        ),
        for (final contribution in contributions.take(5))
          Padding(
            padding: const EdgeInsets.only(top: 3),
            child: Text(
              '• ${_localizedContribution(context, contribution)}',
              style: TextStyle(
                color: context.editorColors.textSecondary,
                fontSize: 11,
              ),
            ),
          ),
      ],
    );
  }
}

class _PhotoGitConflictCard extends StatelessWidget {
  const _PhotoGitConflictCard({
    required this.conflict,
    required this.controller,
    required this.versionLabel,
  });

  final PhotoGitConflict conflict;
  final EditorController controller;
  final String Function(String? editId) versionLabel;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final selected =
        controller.photoGitResolutions[conflict.conflictId] ??
        conflict.resolvedChoice;
    final dependency = conflict.type == 'dependency';
    return DecoratedBox(
      key: Key('photo_git_conflict_${conflict.conflictId}'),
      decoration: BoxDecoration(
        color: context.editorColors.surfaceRaised,
        borderRadius: BorderRadius.circular(AppRadii.small),
        border: Border.all(
          color: context.editorColors.warning.withValues(alpha: 0.55),
        ),
      ),
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.xs),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              '${localizedRegionLabel(l10n, conflict.region)} · '
              '${localizedParameterLabel(l10n, conflict.parameter)}',
              style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w700),
            ),
            if (!dependency) ...[
              const SizedBox(height: 3),
              Text(
                '${l10n.photoGitTargetValue}: '
                '${_formatPhotoGitValue(conflict.targetValue)} · '
                '${l10n.photoGitSourceValue}: '
                '${_formatPhotoGitValue(conflict.sourceValue)}',
                style: TextStyle(
                  color: context.editorColors.textSecondary,
                  fontSize: 11,
                ),
              ),
            ] else if (conflict.laterEditIds.isNotEmpty) ...[
              const SizedBox(height: 3),
              Text(
                '${l10n.photoGitLaterChanges}: '
                '${conflict.laterEditIds.map(versionLabel).join(', ')}',
                style: TextStyle(
                  color: context.editorColors.textSecondary,
                  fontSize: 11,
                ),
              ),
            ],
            const SizedBox(height: 4),
            Wrap(
              spacing: 5,
              runSpacing: 3,
              children: [
                for (final choice in conflict.allowedChoices)
                  ChoiceChip(
                    key: Key(
                      'photo_git_resolution_${conflict.conflictId}_$choice',
                    ),
                    label: Text(switch (choice) {
                      'source' => l10n.photoGitUseSource,
                      'replay' => l10n.photoGitReplayLater,
                      _ => l10n.photoGitKeepTarget,
                    }),
                    selected: selected == choice,
                    onSelected: controller.isPlanningPhotoGit
                        ? null
                        : (value) {
                            if (value) {
                              controller.resolvePhotoGitConflict(
                                conflict.conflictId,
                                choice,
                              );
                            }
                          },
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

String _localizedContribution(
  BuildContext context,
  Map<String, dynamic> contribution,
) {
  final region = localizedRegionLabel(
    context.l10n,
    contribution['region']?.toString() ?? 'all',
  );
  final parameter = localizedParameterLabel(
    context.l10n,
    contribution['parameter']?.toString() ?? '',
  );
  final before = _formatPhotoGitValue(contribution['before_value']);
  final after = _formatPhotoGitValue(contribution['after_value']);
  return '$region · $parameter  $before → $after';
}

String _formatPhotoGitValue(dynamic value) {
  if (value is num) {
    final number = value.toDouble();
    return number == number.roundToDouble()
        ? number.toStringAsFixed(0)
        : number.toStringAsFixed(2);
  }
  return value?.toString() ?? '—';
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
    final safeExplanation = _localizedSafeExplanation(context, edit);
    return PanelScaffold(
      title: controller.hasUncommittedPreview
          ? context.l10n.currentPreview
          : context.l10n.currentAdjustments,
      subtitle:
          '${localizedRegionLabel(context.l10n, edit.region)} · '
          '${localizedEditModeLabel(context.l10n, edit)}',
      icon: Icons.info_outline,
      onClose: onClose,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (edit.editContract != null &&
              !controller.hasUncommittedPreview) ...[
            _EditContractDetails(
              metadata: edit.editContract!,
              schema: controller.editContractSchema,
              edit: edit,
              history: controller.history,
              parameterCatalog: metadataCatalog,
            ),
            const SizedBox(height: AppSpacing.md),
          ],
          if (edit.photoGit != null) ...[
            _PhotoGitDetails(
              metadata: edit.photoGit!,
              history: controller.history,
            ),
            const SizedBox(height: AppSpacing.md),
          ],
          if (edit.style != null) ...[
            _StyleDetails(style: edit.style!),
            const SizedBox(height: AppSpacing.md),
          ],
          if (edit.isDirectStyleEdit && !controller.hasUncommittedPreview) ...[
            Text(
              context.l10n.styleEffectiveParameters(
                (edit.style!.strength * 100).round(),
              ),
              key: const Key('style_effective_parameter_note'),
              style: TextStyle(
                color: context.editorColors.textMuted,
                fontSize: 11,
              ),
            ),
            const SizedBox(height: AppSpacing.sm),
          ],
          if (safeExplanation != null) ...[
            Text(
              safeExplanation,
              style: TextStyle(color: context.editorColors.textSecondary),
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
                      localizedParameterLabel(
                        context.l10n,
                        entry.key,
                        fallback: metadataCatalog.metadataFor(entry.key)!.label,
                      ),
                      style: TextStyle(
                        color: context.editorColors.textSecondary,
                      ),
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
            Text(
              context.l10n.noManualParameters,
              style: TextStyle(color: context.editorColors.textMuted),
            ),
        ],
      ),
    );
  }
}

class _EditContractDetails extends StatelessWidget {
  const _EditContractDetails({
    required this.metadata,
    required this.schema,
    required this.edit,
    required this.history,
    required this.parameterCatalog,
  });

  final EditContractMetadata metadata;
  final EditContractSchema? schema;
  final EditHistoryItem edit;
  final List<EditHistoryItem> history;
  final ParameterMetadataCatalog parameterCatalog;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final colors = context.editorColors;
    final isAdjusted = metadata.wasAdjusted;
    return DecoratedBox(
      key: const Key('edit_contract_details'),
      decoration: BoxDecoration(
        color: colors.accentSoft,
        borderRadius: BorderRadius.circular(AppRadii.small),
        border: Border.all(color: colors.accent.withValues(alpha: 0.4)),
      ),
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.sm),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Icon(
                  Icons.verified_user_outlined,
                  color: colors.accentBright,
                  size: 19,
                ),
                const SizedBox(width: AppSpacing.xs),
                Expanded(
                  child: Text(
                    l10n.contractDetailsTitle,
                    style: const TextStyle(fontWeight: FontWeight.w700),
                  ),
                ),
                _ContractPassChip(
                  passed: metadata.checks.every((check) => check.passed),
                ),
              ],
            ),
            const SizedBox(height: AppSpacing.xs),
            Text(
              isAdjusted
                  ? l10n.contractStatusAdjusted
                  : l10n.contractStatusPassed,
              style: TextStyle(color: colors.textSecondary, fontSize: 12),
            ),
            _ContractDetailRow(
              label: l10n.contractRequestedScale,
              value: _formatScale(metadata.requestedScale),
            ),
            _ContractDetailRow(
              label: l10n.contractAppliedScale,
              value: _formatScale(metadata.appliedScale),
            ),
            _ContractDetailRow(
              label: l10n.contractTargetVersion,
              value: _historyVersion(metadata.targetEditId),
            ),
            _ContractDetailRow(
              label: l10n.contractParentVersion,
              value: _historyVersion(edit.parentEditId),
            ),
            const SizedBox(height: AppSpacing.md),
            _ContractSectionTitle(l10n.contractConstraints),
            if (metadata.constraints.isEmpty)
              Text(
                l10n.contractNoChecks,
                style: TextStyle(color: colors.textMuted, fontSize: 11),
              )
            else
              for (final constraint in metadata.constraints)
                _ContractConstraintCard(constraint: constraint, schema: schema),
            const SizedBox(height: AppSpacing.md),
            _ContractSectionTitle(l10n.contractChecks),
            if (metadata.checks.isEmpty)
              Text(
                l10n.contractNoChecks,
                style: TextStyle(color: colors.textMuted, fontSize: 11),
              )
            else
              for (final check in metadata.checks)
                _ContractCheckCard(check: check, schema: schema),
            if (metadata.requestedParameters.isNotEmpty) ...[
              const SizedBox(height: AppSpacing.md),
              _ContractSectionTitle(l10n.contractRequestedParameters),
              _ContractParameterList(
                parameters: metadata.requestedParameters,
                catalog: parameterCatalog,
              ),
            ],
            if (metadata.actualParameters.isNotEmpty) ...[
              const SizedBox(height: AppSpacing.sm),
              _ContractSectionTitle(l10n.contractActualParameters),
              _ContractParameterList(
                parameters: metadata.actualParameters,
                catalog: parameterCatalog,
              ),
            ],
            const SizedBox(height: AppSpacing.md),
            _ContractSectionTitle(l10n.contractVersions),
            if (metadata.schemaVersion != null)
              _ContractDetailRow(
                label: 'Contract',
                value: metadata.schemaVersion!,
              ),
            if (metadata.semanticRegistryVersion != null)
              _ContractDetailRow(
                label: 'Semantic registry',
                value: metadata.semanticRegistryVersion!,
              ),
            if (metadata.metricRegistryVersion != null)
              _ContractDetailRow(
                label: 'Metric registry',
                value: metadata.metricRegistryVersion!,
              ),
            if (metadata.searchPolicyVersion != null)
              _ContractDetailRow(
                label: 'Search policy',
                value: metadata.searchPolicyVersion!,
              ),
            if (metadata.timings.isNotEmpty) ...[
              const SizedBox(height: AppSpacing.sm),
              _ContractSectionTitle(l10n.contractVerificationTime),
              for (final timing in metadata.timings.entries)
                if (timing.value is num)
                  _ContractDetailRow(
                    label: _humanizeContractIdentifier(timing.key),
                    value: l10n.contractMilliseconds(
                      (timing.value as num).toStringAsFixed(1),
                    ),
                  ),
            ],
          ],
        ),
      ),
    );
  }

  String _historyVersion(String? editId) {
    if (editId == null || editId == EditorController.originalParentSentinel) {
      return editId == null ? '—' : 'Original';
    }
    final index = history.indexWhere((item) => item.editId == editId);
    return index < 0 ? editId : 'v${index + 1}';
  }

  String _formatScale(double? scale) {
    return scale == null ? '—' : '${(scale * 100).round()}%';
  }
}

class _ContractSectionTitle extends StatelessWidget {
  const _ContractSectionTitle(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: AppSpacing.xs),
      child: Text(
        text,
        style: TextStyle(
          color: context.editorColors.textSecondary,
          fontSize: 12,
          fontWeight: FontWeight.w700,
        ),
      ),
    );
  }
}

class _ContractConstraintCard extends StatelessWidget {
  const _ContractConstraintCard({
    required this.constraint,
    required this.schema,
  });

  final EditContractConstraint constraint;
  final EditContractSchema? schema;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final label = localizedContractMetricLabel(
      l10n,
      schema,
      constraint.metricId,
    );
    final description = localizedContractMetricDescription(
      l10n,
      schema,
      constraint.metricId,
    );
    return _ContractCard(
      key: Key('contract_constraint_${constraint.constraintId}'),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(label, style: const TextStyle(fontWeight: FontWeight.w700)),
          if (description.isNotEmpty)
            Text(
              description,
              style: TextStyle(
                color: context.editorColors.textMuted,
                fontSize: 11,
              ),
            ),
          if (constraint.sourceText?.trim().isNotEmpty == true)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(
                '“${constraint.sourceText!.trim()}”',
                style: TextStyle(
                  color: context.editorColors.textSecondary,
                  fontSize: 11,
                ),
              ),
            ),
          _ContractDetailRow(
            label: localizedRegionLabel(l10n, constraint.subjectRegion),
            value:
                '${localizedContractOperator(l10n, constraint.operator)} · '
                '${localizedContractValue(l10n, schema, constraint.unit, constraint.threshold)}',
          ),
          _ContractDetailRow(
            label: l10n.contractThresholdSource,
            value: localizedContractThresholdSource(
              l10n,
              constraint.thresholdSource,
            ),
          ),
        ],
      ),
    );
  }
}

class _ContractCheckCard extends StatelessWidget {
  const _ContractCheckCard({required this.check, required this.schema});

  final EditContractCheck check;
  final EditContractSchema? schema;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    return _ContractCard(
      key: Key('contract_check_${check.constraintId}'),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  localizedContractMetricLabel(l10n, schema, check.metricId),
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
              ),
              _ContractPassChip(passed: check.passed),
            ],
          ),
          _ContractDetailRow(
            label: l10n.contractThreshold,
            value:
                '${localizedContractOperator(l10n, check.operator)} · '
                '${localizedContractValue(l10n, schema, check.unit, check.effectiveThreshold)}',
          ),
          _ContractDetailRow(
            label: l10n.contractThresholdSource,
            value: localizedContractThresholdSource(
              l10n,
              check.thresholdSource,
            ),
          ),
          _ContractDetailRow(
            label: l10n.contractBaseline,
            value: localizedContractValue(
              l10n,
              schema,
              check.unit,
              check.baselineValue,
            ),
          ),
          _ContractDetailRow(
            label: l10n.contractActual,
            value: localizedContractValue(
              l10n,
              schema,
              check.unit,
              check.candidateValue,
            ),
          ),
          _ContractDetailRow(
            label: l10n.contractMetricVersion,
            value: check.metricVersion.isEmpty ? '—' : check.metricVersion,
          ),
        ],
      ),
    );
  }
}

class _ContractCard extends StatelessWidget {
  const _ContractCard({super.key, required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: AppSpacing.xs),
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: context.editorColors.surface.withValues(alpha: 0.72),
          borderRadius: BorderRadius.circular(AppRadii.small),
          border: Border.all(color: context.editorColors.border),
        ),
        child: Padding(
          padding: const EdgeInsets.all(AppSpacing.sm),
          child: child,
        ),
      ),
    );
  }
}

class _ContractPassChip extends StatelessWidget {
  const _ContractPassChip({required this.passed});

  final bool passed;

  @override
  Widget build(BuildContext context) {
    final color = passed
        ? context.editorColors.success
        : context.editorColors.error;
    return DecoratedBox(
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(passed ? Icons.check : Icons.close, color: color, size: 12),
            const SizedBox(width: 3),
            Text(
              passed
                  ? context.l10n.contractCheckPassed
                  : context.l10n.contractCheckFailed,
              style: TextStyle(
                color: color,
                fontSize: 10,
                fontWeight: FontWeight.w700,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _ContractDetailRow extends StatelessWidget {
  const _ContractDetailRow({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 5),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: Text(
              label,
              style: TextStyle(
                color: context.editorColors.textMuted,
                fontSize: 11,
              ),
            ),
          ),
          const SizedBox(width: AppSpacing.xs),
          Flexible(
            flex: 2,
            child: Text(
              value,
              textAlign: TextAlign.end,
              style: TextStyle(
                color: context.editorColors.textSecondary,
                fontSize: 11,
                fontWeight: FontWeight.w600,
                fontFeatures: const [FontFeature.tabularFigures()],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _ContractParameterList extends StatelessWidget {
  const _ContractParameterList({
    required this.parameters,
    required this.catalog,
  });

  final Map<String, dynamic> parameters;
  final ParameterMetadataCatalog catalog;

  @override
  Widget build(BuildContext context) {
    final entries = parameters.entries.where((entry) => entry.value is num);
    return Column(
      children: [
        for (final entry in entries)
          _ContractDetailRow(
            label: localizedParameterLabel(
              context.l10n,
              entry.key,
              fallback: catalog.metadataFor(entry.key)?.label,
            ),
            value:
                catalog
                    .metadataFor(entry.key)
                    ?.formatValue((entry.value as num).toDouble()) ??
                (entry.value as num).toStringAsFixed(3),
          ),
      ],
    );
  }
}

String _humanizeContractIdentifier(String value) {
  final text = value.trim().replaceAll(RegExp(r'[_-]+'), ' ');
  return text.isEmpty ? value : text[0].toUpperCase() + text.substring(1);
}

class _PhotoGitDetails extends StatelessWidget {
  const _PhotoGitDetails({required this.metadata, required this.history});

  final PhotoGitMetadata metadata;
  final List<EditHistoryItem> history;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final isMerge = metadata.operation == PhotoGitOperation.merge;
    final source = metadata.sourceEditIds.isEmpty
        ? null
        : metadata.sourceEditIds.first;
    return DecoratedBox(
      key: const Key('photo_git_details'),
      decoration: BoxDecoration(
        color: context.editorColors.accentSoft,
        borderRadius: BorderRadius.circular(AppRadii.small),
        border: Border.all(
          color: context.editorColors.accent.withValues(alpha: 0.35),
        ),
      ),
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.sm),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Icon(
                  isMerge ? Icons.call_merge : Icons.undo,
                  color: context.editorColors.accentBright,
                  size: 18,
                ),
                const SizedBox(width: AppSpacing.xs),
                Text(
                  isMerge ? l10n.photoGitMerge : l10n.photoGitSelectiveRevert,
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
              ],
            ),
            const SizedBox(height: AppSpacing.xs),
            _PhotoGitDetailRow(
              label: l10n.photoGitTarget,
              value: _historyVersion(context, metadata.targetEditId),
            ),
            if (source != null)
              _PhotoGitDetailRow(
                label: l10n.photoGitSource,
                value: _historyVersion(context, source),
              ),
            if (metadata.revertedEditId != null)
              _PhotoGitDetailRow(
                label: l10n.photoGitRevertStep,
                value: _historyVersion(context, metadata.revertedEditId!),
              ),
            if (metadata.commonAncestorEditId != null)
              _PhotoGitDetailRow(
                label: l10n.photoGitCommonAncestor,
                value: _historyVersion(context, metadata.commonAncestorEditId!),
              ),
            _PhotoGitDetailRow(
              label: l10n.photoGitAdded,
              value: metadata.appliedContributions.isEmpty
                  ? '0'
                  : metadata.appliedContributions
                        .take(4)
                        .map((item) => _localizedContribution(context, item))
                        .join('\n'),
            ),
            _PhotoGitDetailRow(
              label: l10n.photoGitRemoved,
              value: metadata.removedContributions.isEmpty
                  ? '0'
                  : metadata.removedContributions
                        .take(4)
                        .map((item) => _localizedContribution(context, item))
                        .join('\n'),
            ),
            if (metadata.resolutions.isNotEmpty)
              _PhotoGitDetailRow(
                label: l10n.photoGitResolutions,
                value: metadata.resolutions.entries
                    .map(
                      (entry) =>
                          '${_localizedConflictScope(context, entry.key)}: '
                          '${_localizedResolution(context, entry.value)}',
                    )
                    .join('\n'),
              ),
            if (metadata.schemaVersion != null)
              _PhotoGitDetailRow(
                label: l10n.photoGitSchema,
                value: metadata.schemaVersion!,
              ),
            if (metadata.planHash != null)
              _PhotoGitDetailRow(
                label: l10n.photoGitPlanHash,
                value: metadata.planHash!.substring(
                  0,
                  metadata.planHash!.length.clamp(0, 12),
                ),
              ),
          ],
        ),
      ),
    );
  }

  String _historyVersion(BuildContext context, String editId) {
    if (editId == EditorController.originalParentSentinel) {
      return context.l10n.labelOriginal;
    }
    final index = history.indexWhere((edit) => edit.editId == editId);
    return index < 0 ? editId : 'v${index + 1}';
  }

  String _localizedConflictScope(BuildContext context, String conflictId) {
    final rawScope = conflictId.contains(':')
        ? conflictId.substring(conflictId.indexOf(':') + 1)
        : conflictId;
    final parts = rawScope.split('|');
    if (parts.length < 3) {
      return conflictId;
    }
    return '${localizedRegionLabel(context.l10n, parts.first)} · '
        '${localizedParameterLabel(context.l10n, parts.last)}';
  }

  String _localizedResolution(BuildContext context, dynamic value) {
    return switch (value?.toString()) {
      'source' => context.l10n.photoGitUseSource,
      'replay' => context.l10n.photoGitReplayLater,
      _ => context.l10n.photoGitKeepTarget,
    };
  }
}

class _PhotoGitDetailRow extends StatelessWidget {
  const _PhotoGitDetailRow({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 112,
            child: Text(
              label,
              style: TextStyle(
                color: context.editorColors.textMuted,
                fontSize: 11,
              ),
            ),
          ),
          Expanded(
            child: Text(
              value,
              style: TextStyle(
                color: context.editorColors.textSecondary,
                fontSize: 11,
                fontWeight: FontWeight.w600,
              ),
            ),
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
    final colors = context.editorColors;
    final styleName = localizedHistoryStyleName(context.l10n, style);
    final strengthPercent = (style.strength * 100).round();
    return DecoratedBox(
      key: const Key('active_style_details'),
      decoration: BoxDecoration(
        color: colors.accentSoft,
        borderRadius: BorderRadius.circular(AppRadii.small),
        border: Border.all(color: colors.accent.withValues(alpha: 0.35)),
      ),
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.sm),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Icon(
                  Icons.palette_outlined,
                  size: 18,
                  color: colors.accentBright,
                ),
                const SizedBox(width: AppSpacing.xs),
                Expanded(
                  child: Text(
                    styleName,
                    style: const TextStyle(fontWeight: FontWeight.w700),
                  ),
                ),
                Text(
                  '$strengthPercent%',
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
              ],
            ),
            const SizedBox(height: 4),
            Text(
              '${localizedStyleFamilyLabel(context.l10n, style.family)} · '
              '${style.styleId}@${style.version}',
              style: TextStyle(color: colors.textSecondary, fontSize: 12),
            ),
            const SizedBox(height: 2),
            Text(
              '${context.l10n.styleUnderstanding(styleName, strengthPercent)} '
              'renderer ${style.rendererVersion}',
              style: TextStyle(color: colors.textMuted, fontSize: 11),
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
    final colors = context.editorColors;
    final l10n = context.l10n;
    final policy = edit.adaptivePolicyVersion;
    final converged = operations.isNotEmpty
        ? operations.every((operation) => operation.converged == true)
        : edit.adaptiveConverged == true;
    final status = operations.length > 1
        ? l10n.adjustmentCount(operations.length)
        : edit.adaptiveApplied == false
        ? l10n.adaptiveIntervalReset
        : converged
        ? l10n.adaptiveConverged
        : l10n.adaptiveContinue;

    return Container(
      key: const Key('adaptive_details'),
      padding: const EdgeInsets.all(AppSpacing.sm),
      decoration: BoxDecoration(
        color: colors.accentSoft,
        borderRadius: BorderRadius.circular(AppRadii.small),
        border: Border.all(color: colors.accent.withValues(alpha: 0.35)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Icon(Icons.tune, size: 18, color: colors.accentBright),
              const SizedBox(width: AppSpacing.xs),
              Expanded(
                child: Text(
                  l10n.adaptiveFineTune,
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
              ),
              Text(
                status,
                key: const Key('adaptive_converged'),
                style: TextStyle(
                  color: converged == true
                      ? colors.success
                      : colors.textSecondary,
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
              text: localizedAdaptiveReasonLabel(
                context.l10n,
                edit.adaptiveReason!,
              ),
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
    final colors = context.editorColors;
    final l10n = context.l10n;
    return DecoratedBox(
      key: Key('adaptive_operation_${index + 1}_$axis'),
      decoration: BoxDecoration(
        color: colors.surface.withValues(alpha: 0.55),
        borderRadius: BorderRadius.circular(AppRadii.small),
        border: Border.all(color: colors.border),
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
                _AdaptiveTag(
                  key: _key('axis'),
                  text: localizedParameterLabel(
                    context.l10n,
                    operation.axis,
                    fallback: metadata.label,
                  ),
                ),
                _AdaptiveTag(
                  key: _key('region'),
                  text: localizedRegionLabel(context.l10n, operation.region),
                ),
                if (operation.reason != null)
                  _AdaptiveTag(
                    key: _key('reason'),
                    text: localizedAdaptiveReasonLabel(
                      context.l10n,
                      operation.reason!,
                    ),
                  ),
              ],
            ),
            if (operation.deltaFromParent != null)
              _AdaptiveValueRow(
                key: _key('delta'),
                label: l10n.relativeAdjustment,
                value: metadata.formatValue(
                  operation.deltaFromParent!,
                  signed: true,
                ),
              ),
            if (operation.currentValue != null || operation.nextValue != null)
              _AdaptiveValueRow(
                key: _key('current_next'),
                label: l10n.candidateValue,
                value:
                    '${_formatAdaptiveOptional(metadata, operation.currentValue)} → '
                    '${_formatAdaptiveOptional(metadata, operation.nextValue)}',
              ),
            if (operation.lowerBound != null || operation.upperBound != null)
              _AdaptiveValueRow(
                key: _key('bounds'),
                label: l10n.currentBounds,
                value:
                    '${operation.lowerBound == null ? '−∞' : metadata.formatValue(operation.lowerBound!)} ～ '
                    '${operation.upperBound == null ? '+∞' : metadata.formatValue(operation.upperBound!)}',
              ),
            if (operation.stepBefore != null || operation.stepAfter != null)
              _AdaptiveValueRow(
                key: _key('steps'),
                label: metadata.usesLogTransform
                    ? l10n.stepSizeWithTransform(metadata.transform)
                    : l10n.stepSize,
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
        color: context.editorColors.surface.withValues(alpha: 0.72),
        borderRadius: BorderRadius.circular(AppRadii.small),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 4),
        child: Text(
          text,
          style: TextStyle(
            color: context.editorColors.textSecondary,
            fontSize: 11,
          ),
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
              style: TextStyle(
                color: context.editorColors.textSecondary,
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
      decoration: BoxDecoration(
        border: Border(bottom: BorderSide(color: context.editorColors.border)),
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
            Icon(icon, size: 21, color: context.editorColors.accentBright),
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
                      style: TextStyle(
                        color: context.editorColors.textMuted,
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
              tooltip: context.l10n.collapse,
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
    final colors = context.editorColors;
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
            ? colors.error.withValues(alpha: 0.1)
            : colors.success.withValues(alpha: 0.1),
        borderRadius: BorderRadius.circular(AppRadii.small),
        border: Border.all(
          color: isError
              ? colors.error.withValues(alpha: 0.35)
              : colors.success.withValues(alpha: 0.35),
        ),
      ),
      child: Row(
        children: [
          Icon(
            isError ? Icons.error_outline : Icons.check_circle_outline,
            color: isError ? colors.error : colors.success,
            size: 19,
          ),
          const SizedBox(width: AppSpacing.xs),
          Expanded(
            child: Text(
              message,
              style: TextStyle(
                color: isError ? colors.error : colors.success,
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
    final localizedLabel = localizedParameterLabel(
      context.l10n,
      spec.key,
      fallback: spec.label,
    );
    return Padding(
      key: Key('manual_slider_${spec.key}'),
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.xs),
      child: Column(
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  localizedLabel,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontWeight: FontWeight.w500),
                ),
              ),
              Text(
                spec.format(value),
                key: Key('manual_value_${spec.key}'),
                style: TextStyle(
                  color: context.editorColors.textSecondary,
                  fontSize: 13,
                  fontWeight: FontWeight.w600,
                  fontFeatures: [FontFeature.tabularFigures()],
                ),
              ),
              const SizedBox(width: 2),
              IconButton(
                tooltip: context.l10n.resetParameter(localizedLabel),
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
    this.sourceVersion,
    this.revertedVersion,
    required this.selected,
    required this.onTap,
  });

  final EditHistoryItem item;
  final ParameterMetadataCatalog metadataCatalog;
  final int version;
  final int? parentVersion;
  final int? sourceVersion;
  final int? revertedVersion;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final summary = localizedCompactParameterSummary(
      context.l10n,
      item.parametersForDisplay(metadataCatalog),
      metadataCatalog: metadataCatalog,
    );
    final parameterSummary = item.isDirectStyleEdit && summary.isNotEmpty
        ? context.l10n.equivalentParameters(summary)
        : summary;
    final colors = context.editorColors;
    return Material(
      color: selected ? colors.accentSoft : colors.surfaceRaised,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppRadii.small),
        side: BorderSide(color: selected ? colors.accent : colors.border),
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
                  errorBuilder: (_, _, _) => SizedBox(
                    width: 64,
                    height: 64,
                    child: ColoredBox(
                      color: colors.surfaceSoft,
                      child: Icon(
                        Icons.broken_image_outlined,
                        color: colors.textMuted,
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
                            context.l10n.historyVersionMode(
                              version,
                              localizedEditModeLabel(context.l10n, item),
                            ),
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              fontWeight: FontWeight.w700,
                              fontSize: 13,
                            ),
                          ),
                        ),
                        if (selected)
                          Icon(
                            Icons.check_circle,
                            size: 18,
                            color: colors.accentBright,
                          ),
                      ],
                    ),
                    const SizedBox(height: 4),
                    Align(
                      alignment: Alignment.centerLeft,
                      child: Wrap(
                        spacing: 4,
                        runSpacing: 3,
                        children: [
                          _HistoryBranchBadge(
                            key: Key('history_branch_badge_${item.editId}'),
                            isRoot: item.parentEditId == null,
                            parentVersion: parentVersion,
                          ),
                          if (item.editContract?.isSuccessful == true)
                            _HistoryProvenanceBadge(
                              key: Key('history_contract_badge_${item.editId}'),
                              icon: Icons.verified_user_outlined,
                              label: localizedContractCompactSummary(
                                context.l10n,
                                item.editContract!,
                              ),
                            ),
                          if (sourceVersion != null)
                            _HistoryProvenanceBadge(
                              icon: Icons.call_merge,
                              label:
                                  '${context.l10n.photoGitMergedFrom} '
                                  'v$sourceVersion',
                            ),
                          if (revertedVersion != null)
                            _HistoryProvenanceBadge(
                              icon: Icons.undo,
                              label:
                                  '${context.l10n.photoGitRevertedFrom} '
                                  'v$revertedVersion',
                            ),
                        ],
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      localizedEditDisplayTitle(context.l10n, item),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        color: colors.textSecondary,
                        fontSize: 12,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      '${localizedRegionLabel(context.l10n, item.region)}'
                      '${parameterSummary.isEmpty ? '' : ' · $parameterSummary'}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(color: colors.textMuted, fontSize: 11),
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

class _HistoryProvenanceBadge extends StatelessWidget {
  const _HistoryProvenanceBadge({
    super.key,
    required this.icon,
    required this.label,
  });

  final IconData icon;
  final String label;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        color: context.editorColors.accentSoft,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 11, color: context.editorColors.accentBright),
            const SizedBox(width: 3),
            Text(
              label,
              style: TextStyle(
                color: context.editorColors.textSecondary,
                fontSize: 10,
              ),
            ),
          ],
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
        ? context.l10n.rootBranch
        : parentVersion == null
        ? context.l10n.continuesParent
        : context.l10n.continuesVersion(parentVersion!);
    return DecoratedBox(
      decoration: BoxDecoration(
        color: context.editorColors.surfaceSoft,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
        child: Text(
          label,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: TextStyle(color: context.editorColors.textMuted, fontSize: 10),
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
            color: context.editorColors.imageStage,
            border: Border.all(color: context.editorColors.border),
            borderRadius: BorderRadius.circular(AppRadii.medium),
          ),
          child: bytes == null
              ? Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(
                        Icons.add_photo_alternate_outlined,
                        color: context.editorColors.imageStageMuted,
                      ),
                      const SizedBox(height: 8),
                      Text(
                        context.l10n.referenceNotSelected,
                        style: TextStyle(
                          color: context.editorColors.imageStageMuted,
                        ),
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
      decoration: BoxDecoration(
        color: context.editorColors.surface,
        border: Border(top: BorderSide(color: context.editorColors.border)),
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
                  label: Text(
                    context.l10n.actionReset,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
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
                      ? SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(
                            strokeWidth: 2,
                            color: Theme.of(context).colorScheme.onPrimary,
                          ),
                        )
                      : const Icon(Icons.check),
                  label: Text(
                    controller.isCommittingManual
                        ? context.l10n.actionApplying
                        : context.l10n.actionApply,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
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
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xs),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.circle, size: 7, color: context.editorColors.warning),
          const SizedBox(width: 5),
          Text(
            context.l10n.notApplied,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(
              color: context.editorColors.warning,
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
            Icon(icon, size: 36, color: context.editorColors.textMuted),
            const SizedBox(height: AppSpacing.md),
            Text(
              message,
              textAlign: TextAlign.center,
              style: TextStyle(color: context.editorColors.textSecondary),
            ),
          ],
        ),
      ),
    );
  }
}

bool _usesEnglishUi(BuildContext context) =>
    Localizations.localeOf(context).languageCode == 'en';

bool _hasControllerError(EditorController controller) =>
    controller.errorPresentation != null || controller.errorMessage != null;

String? _localizedControllerError(
  BuildContext context,
  EditorController controller,
) {
  if (!_hasControllerError(controller)) {
    return null;
  }
  return localizedPresentationMessage(
    context.l10n,
    controller.errorPresentation,
    legacyFallback: controller.errorMessage,
  );
}

String? _localizedControllerMessage(
  BuildContext context,
  EditorController controller,
) {
  final error = _localizedControllerError(context, controller);
  if (error != null) {
    return error;
  }
  if (controller.statusPresentation == null &&
      controller.statusMessage == null) {
    return null;
  }
  return localizedPresentationMessage(
    context.l10n,
    controller.statusPresentation,
    legacyFallback: controller.statusMessage,
  );
}

String _localizedCatalogStyleSecondaryLabel(
  BuildContext context,
  StyleCatalogItem style,
) {
  final family = localizedStyleFamilyLabel(context.l10n, style.family);
  if (_usesEnglishUi(context)) {
    return family;
  }
  final englishName = style.displayNameEn.trim();
  return englishName.isEmpty ? family : '$family · $englishName';
}

String _localizedManualDisabledReason(
  BuildContext context,
  EditorController controller,
) {
  final edit = controller.selectedEdit;
  if (edit == null) {
    return context.l10n.manualUnavailableNeedPrompt;
  }
  if (edit.editMode == 'reference') {
    return context.l10n.manualUnavailableReference;
  }
  if (edit.engine.toLowerCase() != 'opencv') {
    return context.l10n.manualUnavailableEngine;
  }
  return context.l10n.manualUnavailableGeneric;
}

String? _localizedSafeExplanation(BuildContext context, EditHistoryItem item) {
  final explanation = item.explanation?.trim();
  if (explanation == null || explanation.isEmpty || _usesEnglishUi(context)) {
    return null;
  }
  return explanation;
}
