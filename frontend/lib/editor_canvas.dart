import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'app_theme.dart';
import 'editor_controller.dart';
import 'editor_localizations.dart';
import 'l10n/l10n_context.dart';

class EditorCanvas extends StatefulWidget {
  const EditorCanvas({
    super.key,
    required this.controller,
    required this.sideBySide,
    required this.onPickOriginal,
    required this.onOpenDetails,
  });

  final EditorController controller;
  final bool sideBySide;
  final VoidCallback onPickOriginal;
  final VoidCallback onOpenDetails;

  @override
  State<EditorCanvas> createState() => _EditorCanvasState();
}

class _EditorCanvasState extends State<EditorCanvas> {
  late final TransformationController _comparisonTransform;
  late Object? _originalIdentity;
  Timer? _longPressHintTimer;
  bool _holdingOriginal = false;
  bool _longPressHintShown = false;
  bool _showLongPressHint = false;

  @override
  void initState() {
    super.initState();
    _comparisonTransform = TransformationController();
    _originalIdentity = _identityFor(widget.controller);
  }

  @override
  void didUpdateWidget(covariant EditorCanvas oldWidget) {
    super.didUpdateWidget(oldWidget);
    final nextIdentity = _identityFor(widget.controller);
    final sourceChanged =
        !identical(oldWidget.controller, widget.controller) ||
        !_sameIdentity(_originalIdentity, nextIdentity);
    if (!sourceChanged) {
      return;
    }

    _originalIdentity = nextIdentity;
    _longPressHintTimer?.cancel();
    _longPressHintTimer = null;
    _holdingOriginal = false;
    _showLongPressHint = false;
    _resetComparisonTransform();
  }

  @override
  void dispose() {
    _longPressHintTimer?.cancel();
    _comparisonTransform.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final controller = widget.controller;
    return Column(
      key: const Key('editor_canvas'),
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Expanded(
          child: controller.hasOriginal
              ? widget.sideBySide
                    ? _buildSideBySide(controller)
                    : _buildSingleCanvas(controller)
              : _EmptyCanvas(onPickOriginal: widget.onPickOriginal),
        ),
        const SizedBox(height: AppSpacing.sm),
        _CurrentSummary(
          controller: controller,
          onTap: controller.selectedEdit == null ? null : widget.onOpenDetails,
        ),
      ],
    );
  }

  Widget _buildSideBySide(EditorController controller) {
    final l10n = context.l10n;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Expanded(
          child: _ImageStage(
            key: const Key('original_image'),
            label: l10n.labelOriginal,
            bytes: controller.originalImageBytes,
            imageUrl: controller.originalImageUrl,
          ),
        ),
        const SizedBox(width: AppSpacing.sm),
        Expanded(
          child: _ImageStage(
            key: const Key('result_image'),
            label: controller.hasUncommittedPreview
                ? l10n.labelPreview
                : l10n.labelResult,
            imageUrl: controller.currentResultUrl,
            emptyLabel: l10n.resultAppearsHere,
            isProcessing: controller.isProcessing || controller.isPreviewing,
          ),
        ),
      ],
    );
  }

  Widget _buildSingleCanvas(EditorController controller) {
    final l10n = context.l10n;
    final hasResult = controller.currentResultUrl != null;
    _scheduleLongPressHint(hasResult);
    final view = !hasResult || _holdingOriginal
        ? ComparisonView.original
        : controller.comparisonView;

    return LayoutBuilder(
      builder: (context, constraints) {
        return Stack(
          clipBehavior: Clip.hardEdge,
          children: [
            Positioned.fill(
              child: _CompositeImageStage(
                view: view,
                transformation: _comparisonTransform,
                originalBytes: controller.originalImageBytes,
                originalUrl: controller.originalImageUrl,
                baselineBytes: controller.comparisonBaselineBytes,
                baselineUrl: controller.comparisonBaselineUrl,
                resultUrl: controller.currentResultUrl,
                split: controller.comparisonSplit,
                resultLabel: controller.hasUncommittedPreview
                    ? l10n.labelPreview
                    : l10n.labelResult,
                isProcessing:
                    view != ComparisonView.original &&
                    (controller.isProcessing || controller.isPreviewing),
              ),
            ),
            Positioned.fill(
              child: GestureDetector(
                onLongPressStart: !hasResult
                    ? null
                    : (_) => setState(() => _holdingOriginal = true),
                onLongPressEnd: !hasResult
                    ? null
                    : (_) => setState(() => _holdingOriginal = false),
                onLongPressCancel: !hasResult
                    ? null
                    : () => setState(() => _holdingOriginal = false),
                onDoubleTap: _resetComparisonTransform,
                child: InteractiveViewer(
                  key: const Key('comparison_interactive_viewer'),
                  transformationController: _comparisonTransform,
                  minScale: 1,
                  maxScale: 4,
                  clipBehavior: Clip.hardEdge,
                  child: SizedBox(
                    width: constraints.maxWidth,
                    height: constraints.maxHeight,
                    child: const ColoredBox(color: Colors.transparent),
                  ),
                ),
              ),
            ),
            if (view == ComparisonView.compare)
              _ComparisonHandle(
                value: controller.comparisonSplit,
                canvasWidth: constraints.maxWidth,
                onChanged: controller.setComparisonSplit,
              ),
            if (view == ComparisonView.compare)
              Positioned(
                top: AppSpacing.sm,
                left: AppSpacing.sm,
                right: AppSpacing.sm,
                child: Center(
                  child: ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 280),
                    child: _ComparisonBaselineToggle(
                      value: controller.comparisonUsesParent
                          ? ComparisonBaseline.parent
                          : ComparisonBaseline.original,
                      parentEnabled: controller.canCompareWithParent,
                      onChanged: controller.setComparisonBaseline,
                    ),
                  ),
                ),
              ),
            if (hasResult)
              Positioned(
                top: view == ComparisonView.compare ? 66 : AppSpacing.sm,
                right: AppSpacing.sm,
                child: _ResetViewButton(onPressed: _resetComparisonTransform),
              ),
            if (_showLongPressHint && hasResult && !_holdingOriginal)
              Positioned(
                left: 0,
                right: 0,
                bottom: 70,
                child: Center(
                  child: _LongPressHint(onDismiss: _dismissLongPressHint),
                ),
              ),
            if (hasResult)
              Positioned(
                left: AppSpacing.sm,
                right: AppSpacing.sm,
                bottom: AppSpacing.md,
                child: Center(
                  child: ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 360),
                    child: _ComparisonToggle(
                      key: const Key('comparison_toggle'),
                      value: controller.comparisonView,
                      onChanged: controller.setComparisonView,
                    ),
                  ),
                ),
              ),
          ],
        );
      },
    );
  }

  void _scheduleLongPressHint(bool hasResult) {
    if (!hasResult || _longPressHintShown) {
      return;
    }
    _longPressHintShown = true;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) {
        return;
      }
      setState(() => _showLongPressHint = true);
      _longPressHintTimer = Timer(
        const Duration(seconds: 5),
        _dismissLongPressHint,
      );
    });
  }

  void _dismissLongPressHint() {
    _longPressHintTimer?.cancel();
    _longPressHintTimer = null;
    if (mounted && _showLongPressHint) {
      setState(() => _showLongPressHint = false);
    }
  }

  void _resetComparisonTransform() {
    _comparisonTransform.value = Matrix4.identity();
  }

  static Object? _identityFor(EditorController controller) {
    return controller.originalImageBytes ?? controller.originalImageUrl;
  }

  static bool _sameIdentity(Object? previous, Object? next) {
    if (previous is String && next is String) {
      return previous == next;
    }
    return identical(previous, next);
  }
}

class _CompositeImageStage extends StatelessWidget {
  const _CompositeImageStage({
    required this.view,
    required this.transformation,
    required this.originalBytes,
    required this.originalUrl,
    required this.baselineBytes,
    required this.baselineUrl,
    required this.resultUrl,
    required this.split,
    required this.resultLabel,
    required this.isProcessing,
  });

  final ComparisonView view;
  final TransformationController transformation;
  final Uint8List? originalBytes;
  final String? originalUrl;
  final Uint8List? baselineBytes;
  final String? baselineUrl;
  final String? resultUrl;
  final double split;
  final String resultLabel;
  final bool isProcessing;

  @override
  Widget build(BuildContext context) {
    final colors = context.editorColors;
    final l10n = context.l10n;
    return ClipRRect(
      borderRadius: BorderRadius.circular(AppRadii.medium),
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: colors.imageStage,
          border: Border.all(color: colors.border),
          borderRadius: BorderRadius.circular(AppRadii.medium),
        ),
        child: Stack(
          fit: StackFit.expand,
          children: [
            if (view == ComparisonView.original) ...[
              _TransformedCanvasImage(
                key: const Key('original_image'),
                transformation: transformation,
                bytes: originalBytes,
                imageUrl: originalUrl,
                emptyLabel: l10n.noImage,
              ),
              _CanvasLabel(
                label: l10n.labelOriginal,
                alignment: Alignment.topLeft,
              ),
            ] else if (view == ComparisonView.result) ...[
              _TransformedCanvasImage(
                key: const Key('result_image'),
                transformation: transformation,
                imageUrl: resultUrl,
                emptyLabel: l10n.resultAppearsHere,
              ),
              _CanvasLabel(label: resultLabel, alignment: Alignment.topLeft),
            ] else ...[
              _TransformedCanvasImage(
                key: const Key('comparison_after_image'),
                transformation: transformation,
                imageUrl: resultUrl,
                emptyLabel: l10n.resultAppearsHere,
              ),
              Positioned.fill(
                child: ClipRect(
                  key: const Key('comparison_before_clip'),
                  clipper: _FractionClipper(split),
                  child: _TransformedCanvasImage(
                    key: const Key('comparison_before_image'),
                    transformation: transformation,
                    bytes: baselineBytes,
                    imageUrl: baselineUrl,
                    emptyLabel: l10n.comparisonParentUnavailable,
                  ),
                ),
              ),
              _CanvasLabel(
                label: l10n.labelBefore,
                alignment: Alignment.topLeft,
              ),
              _CanvasLabel(
                label: l10n.labelAfter,
                alignment: Alignment.topRight,
              ),
            ],
            if (isProcessing)
              const Positioned.fill(child: _ProcessingOverlay()),
          ],
        ),
      ),
    );
  }
}

class _CanvasImage extends StatelessWidget {
  const _CanvasImage({this.bytes, this.imageUrl, required this.emptyLabel});

  final Uint8List? bytes;
  final String? imageUrl;
  final String emptyLabel;

  @override
  Widget build(BuildContext context) {
    final colors = context.editorColors;
    if (bytes != null) {
      return Image.memory(
        bytes!,
        fit: BoxFit.contain,
        alignment: Alignment.center,
        gaplessPlayback: true,
        filterQuality: FilterQuality.medium,
        errorBuilder: (context, error, stackTrace) => const _ImageError(),
      );
    }
    if (imageUrl != null) {
      return Image.network(
        imageUrl!,
        fit: BoxFit.contain,
        alignment: Alignment.center,
        gaplessPlayback: true,
        filterQuality: FilterQuality.medium,
        loadingBuilder: (context, child, progress) {
          if (progress == null) {
            return child;
          }
          return const Center(child: CircularProgressIndicator());
        },
        errorBuilder: (context, error, stackTrace) => const _ImageError(),
      );
    }
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.lg),
        child: Text(
          emptyLabel,
          textAlign: TextAlign.center,
          style: TextStyle(color: colors.imageStageMuted),
        ),
      ),
    );
  }
}

class _TransformedCanvasImage extends StatelessWidget {
  const _TransformedCanvasImage({
    super.key,
    required this.transformation,
    this.bytes,
    this.imageUrl,
    required this.emptyLabel,
  });

  final TransformationController transformation;
  final Uint8List? bytes;
  final String? imageUrl;
  final String emptyLabel;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: transformation,
      builder: (context, child) {
        return Transform(transform: transformation.value, child: child);
      },
      child: _CanvasImage(
        bytes: bytes,
        imageUrl: imageUrl,
        emptyLabel: emptyLabel,
      ),
    );
  }
}

class _CanvasLabel extends StatelessWidget {
  const _CanvasLabel({required this.label, required this.alignment});

  final String label;
  final Alignment alignment;

  @override
  Widget build(BuildContext context) {
    final colors = context.editorColors;
    return Align(
      alignment: alignment,
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.sm),
        child: DecoratedBox(
          decoration: BoxDecoration(
            color: colors.imageStage.withValues(alpha: 0.88),
            borderRadius: BorderRadius.circular(AppRadii.small),
            border: Border.all(
              color: colors.onImageStage.withValues(alpha: 0.22),
            ),
          ),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 7),
            child: Text(
              label,
              style: TextStyle(
                color: colors.onImageStage,
                fontSize: 12,
                fontWeight: FontWeight.w700,
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _FractionClipper extends CustomClipper<Rect> {
  const _FractionClipper(this.fraction);

  final double fraction;

  @override
  Rect getClip(Size size) =>
      Rect.fromLTWH(0, 0, size.width * fraction, size.height);

  @override
  bool shouldReclip(_FractionClipper oldClipper) =>
      oldClipper.fraction != fraction;
}

class _ComparisonHandle extends StatefulWidget {
  const _ComparisonHandle({
    required this.value,
    required this.canvasWidth,
    required this.onChanged,
  });

  final double value;
  final double canvasWidth;
  final ValueChanged<double> onChanged;

  @override
  State<_ComparisonHandle> createState() => _ComparisonHandleState();
}

class _ComparisonHandleState extends State<_ComparisonHandle> {
  final FocusNode _focusNode = FocusNode(debugLabel: 'comparison_handle');
  late double _dragValue;
  bool _hasFocus = false;

  @override
  void initState() {
    super.initState();
    _dragValue = widget.value;
  }

  @override
  void didUpdateWidget(_ComparisonHandle oldWidget) {
    super.didUpdateWidget(oldWidget);
    _dragValue = widget.value;
  }

  @override
  void dispose() {
    _focusNode.dispose();
    super.dispose();
  }

  void _move(double delta) => widget.onChanged(widget.value + delta);

  void _updateDrag(DragUpdateDetails details) {
    if (widget.canvasWidth <= 0) {
      return;
    }
    _dragValue = (_dragValue + (details.delta.dx / widget.canvasWidth))
        .clamp(0.05, 0.95)
        .toDouble();
    widget.onChanged(_dragValue);
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.editorColors;
    final l10n = context.l10n;
    final percentage = (widget.value * 100).round();
    final increasedPercentage = (percentage + 5).clamp(5, 95);
    final decreasedPercentage = (percentage - 5).clamp(5, 95);
    return Positioned(
      key: const Key('comparison_handle'),
      left: (widget.canvasWidth * widget.value) - 22,
      top: 0,
      bottom: 0,
      width: 44,
      child: Semantics(
        slider: true,
        label: l10n.comparisonDragHandle,
        value: l10n.comparisonDragHandleValue(percentage),
        increasedValue: l10n.comparisonDragHandleValue(increasedPercentage),
        decreasedValue: l10n.comparisonDragHandleValue(decreasedPercentage),
        onIncrease: () => _move(0.05),
        onDecrease: () => _move(-0.05),
        child: CallbackShortcuts(
          bindings: <ShortcutActivator, VoidCallback>{
            const SingleActivator(LogicalKeyboardKey.arrowLeft): () =>
                _move(-0.05),
            const SingleActivator(LogicalKeyboardKey.arrowRight): () =>
                _move(0.05),
            const SingleActivator(LogicalKeyboardKey.home): () =>
                widget.onChanged(0.05),
            const SingleActivator(LogicalKeyboardKey.end): () =>
                widget.onChanged(0.95),
          },
          child: Focus(
            focusNode: _focusNode,
            onFocusChange: (value) => setState(() => _hasFocus = value),
            child: GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTapDown: (_) => _focusNode.requestFocus(),
              onHorizontalDragStart: (_) {
                _focusNode.requestFocus();
                _dragValue = widget.value;
              },
              onHorizontalDragUpdate: _updateDrag,
              child: Stack(
                fit: StackFit.expand,
                alignment: Alignment.center,
                children: [
                  Center(
                    child: Container(width: 2, color: colors.onImageStage),
                  ),
                  Center(
                    child: Container(
                      width: 36,
                      height: 36,
                      decoration: BoxDecoration(
                        color: colors.imageStage.withValues(alpha: 0.92),
                        shape: BoxShape.circle,
                        border: Border.all(
                          color: _hasFocus
                              ? colors.accentBright
                              : colors.onImageStage,
                          width: _hasFocus ? 3 : 2,
                        ),
                        boxShadow: [
                          BoxShadow(
                            color: colors.modalBarrier.withValues(alpha: 0.42),
                            blurRadius: 8,
                            offset: Offset(0, 2),
                          ),
                        ],
                      ),
                      child: Icon(
                        Icons.drag_indicator,
                        color: colors.onImageStage,
                        size: 20,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _ComparisonBaselineToggle extends StatelessWidget {
  const _ComparisonBaselineToggle({
    required this.value,
    required this.parentEnabled,
    required this.onChanged,
  });

  final ComparisonBaseline value;
  final bool parentEnabled;
  final ValueChanged<ComparisonBaseline> onChanged;

  @override
  Widget build(BuildContext context) {
    final colors = context.editorColors;
    final l10n = context.l10n;
    return Material(
      key: const Key('comparison_baseline_toggle'),
      color: colors.imageStage.withValues(alpha: 0.92),
      shape: StadiumBorder(
        side: BorderSide(color: colors.onImageStage.withValues(alpha: 0.28)),
      ),
      child: Padding(
        padding: const EdgeInsets.all(3),
        child: Row(
          children: [
            Expanded(
              child: _BaselineOption(
                key: const Key('comparison_baseline_original'),
                label: l10n.comparisonBaselineOriginal,
                selected: value == ComparisonBaseline.original,
                onTap: () => onChanged(ComparisonBaseline.original),
              ),
            ),
            Expanded(
              child: Tooltip(
                message: parentEnabled
                    ? l10n.comparisonBaselineParent
                    : l10n.comparisonParentUnavailable,
                child: _BaselineOption(
                  key: const Key('comparison_baseline_parent'),
                  label: l10n.comparisonBaselineParent,
                  selected: value == ComparisonBaseline.parent,
                  onTap: parentEnabled
                      ? () => onChanged(ComparisonBaseline.parent)
                      : null,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _BaselineOption extends StatelessWidget {
  const _BaselineOption({
    super.key,
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final colors = context.editorColors;
    return Semantics(
      button: true,
      selected: selected,
      enabled: onTap != null,
      child: InkWell(
        borderRadius: BorderRadius.circular(999),
        onTap: onTap,
        child: ConstrainedBox(
          constraints: const BoxConstraints(minHeight: 44),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 7),
            child: Center(
              child: Text(
                label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                textAlign: TextAlign.center,
                style: TextStyle(
                  color: onTap == null
                      ? colors.imageStageMuted.withValues(alpha: 0.55)
                      : selected
                      ? colors.onImageStage
                      : colors.imageStageMuted,
                  fontSize: 12,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _ResetViewButton extends StatelessWidget {
  const _ResetViewButton({required this.onPressed});

  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    final colors = context.editorColors;
    return Tooltip(
      message: context.l10n.resetZoom,
      child: Material(
        color: colors.imageStage.withValues(alpha: 0.92),
        shape: CircleBorder(
          side: BorderSide(color: colors.onImageStage.withValues(alpha: 0.28)),
        ),
        child: IconButton(
          key: const Key('comparison_reset_view'),
          onPressed: onPressed,
          constraints: const BoxConstraints.tightFor(width: 44, height: 44),
          icon: Icon(
            Icons.center_focus_strong,
            size: 20,
            color: colors.onImageStage,
          ),
        ),
      ),
    );
  }
}

class _LongPressHint extends StatelessWidget {
  const _LongPressHint({required this.onDismiss});

  final VoidCallback onDismiss;

  @override
  Widget build(BuildContext context) {
    final colors = context.editorColors;
    final l10n = context.l10n;
    return Semantics(
      button: true,
      label: l10n.dismissHint,
      hint: l10n.holdToSeeOriginal,
      excludeSemantics: true,
      child: Material(
        key: const Key('long_press_original_hint'),
        color: colors.imageStage.withValues(alpha: 0.92),
        shape: StadiumBorder(
          side: BorderSide(color: colors.onImageStage.withValues(alpha: 0.28)),
        ),
        child: InkWell(
          borderRadius: BorderRadius.circular(999),
          onTap: onDismiss,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(12, 8, 10, 8),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(
                  Icons.touch_app_outlined,
                  size: 17,
                  color: colors.onImageStage,
                ),
                const SizedBox(width: 7),
                Flexible(
                  child: Text(
                    l10n.holdToSeeOriginal,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      color: colors.onImageStage,
                      fontSize: 12,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
                const SizedBox(width: 7),
                Icon(Icons.close, size: 15, color: colors.onImageStage),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _EmptyCanvas extends StatelessWidget {
  const _EmptyCanvas({required this.onPickOriginal});

  final VoidCallback onPickOriginal;

  @override
  Widget build(BuildContext context) {
    final colors = context.editorColors;
    return DecoratedBox(
      decoration: BoxDecoration(
        color: colors.imageStage,
        borderRadius: BorderRadius.circular(AppRadii.medium),
        border: Border.all(color: colors.border),
      ),
      child: LayoutBuilder(
        builder: (context, constraints) {
          if (constraints.maxHeight < 280) {
            return _buildShortLandscape(context);
          }
          return _buildStandard(context);
        },
      ),
    );
  }

  Widget _buildStandard(BuildContext context) {
    final colors = context.editorColors;
    final l10n = context.l10n;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.xl),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            _emptyIcon(context, size: 72, iconSize: 32),
            const SizedBox(height: AppSpacing.md),
            Text(
              l10n.selectPhotoToStart,
              style: Theme.of(
                context,
              ).textTheme.titleLarge?.copyWith(color: colors.onImageStage),
            ),
            const SizedBox(height: AppSpacing.xs),
            Text(
              l10n.photoWorkspaceDescription,
              textAlign: TextAlign.center,
              style: TextStyle(color: colors.imageStageMuted),
            ),
            const SizedBox(height: AppSpacing.lg),
            _pickButton(context),
          ],
        ),
      ),
    );
  }

  Widget _buildShortLandscape(BuildContext context) {
    final colors = context.editorColors;
    final l10n = context.l10n;
    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.sm,
      ),
      child: Row(
        children: [
          _emptyIcon(context, size: 52, iconSize: 25),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  l10n.selectPhotoToStart,
                  style: Theme.of(
                    context,
                  ).textTheme.titleMedium?.copyWith(color: colors.onImageStage),
                ),
                const SizedBox(height: 3),
                Text(
                  l10n.photoWorkspaceCompactDescription,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(color: colors.imageStageMuted, fontSize: 12),
                ),
              ],
            ),
          ),
          const SizedBox(width: AppSpacing.md),
          _pickButton(context),
        ],
      ),
    );
  }

  Widget _emptyIcon(
    BuildContext context, {
    required double size,
    required double iconSize,
  }) {
    final colors = context.editorColors;
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        color: colors.onImageStage.withValues(alpha: 0.10),
        shape: BoxShape.circle,
      ),
      child: Icon(
        Icons.add_photo_alternate_outlined,
        size: iconSize,
        color: colors.imageStageMuted,
      ),
    );
  }

  Widget _pickButton(BuildContext context) {
    return FilledButton.icon(
      key: const Key('pick_original_button'),
      onPressed: onPickOriginal,
      icon: const Icon(Icons.photo_library_outlined),
      label: Text(context.l10n.selectOriginal),
    );
  }
}

class _ImageStage extends StatelessWidget {
  const _ImageStage({
    super.key,
    required this.label,
    this.bytes,
    this.imageUrl,
    this.emptyLabel,
    this.isProcessing = false,
  });

  final String label;
  final Uint8List? bytes;
  final String? imageUrl;
  final String? emptyLabel;
  final bool isProcessing;

  @override
  Widget build(BuildContext context) {
    final hasImage = bytes != null || imageUrl != null;
    final colors = context.editorColors;
    return ClipRRect(
      borderRadius: BorderRadius.circular(AppRadii.medium),
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: colors.imageStage,
          border: Border.all(color: colors.border),
          borderRadius: BorderRadius.circular(AppRadii.medium),
        ),
        child: Stack(
          fit: StackFit.expand,
          children: [
            if (bytes != null)
              Image.memory(
                bytes!,
                fit: BoxFit.contain,
                gaplessPlayback: true,
                filterQuality: FilterQuality.medium,
                errorBuilder: (context, error, stackTrace) =>
                    const _ImageError(),
              )
            else if (imageUrl != null)
              Image.network(
                imageUrl!,
                fit: BoxFit.contain,
                gaplessPlayback: true,
                filterQuality: FilterQuality.medium,
                loadingBuilder: (context, child, progress) {
                  if (progress == null) {
                    return child;
                  }
                  return const Center(child: CircularProgressIndicator());
                },
                errorBuilder: (context, error, stackTrace) {
                  return const _ImageError();
                },
              )
            else
              Center(
                child: Padding(
                  padding: const EdgeInsets.all(AppSpacing.lg),
                  child: Text(
                    emptyLabel ?? context.l10n.noImage,
                    textAlign: TextAlign.center,
                    style: TextStyle(color: colors.imageStageMuted),
                  ),
                ),
              ),
            Positioned(
              left: AppSpacing.sm,
              top: AppSpacing.sm,
              child: DecoratedBox(
                decoration: BoxDecoration(
                  color: colors.imageStage.withValues(alpha: 0.88),
                  borderRadius: BorderRadius.circular(AppRadii.small),
                  border: Border.all(
                    color: colors.onImageStage.withValues(alpha: 0.22),
                  ),
                ),
                child: Padding(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 11,
                    vertical: 7,
                  ),
                  child: Text(
                    label,
                    style: TextStyle(
                      color: colors.onImageStage,
                      fontSize: 12,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
              ),
            ),
            if (isProcessing && hasImage)
              const Positioned.fill(child: _ProcessingOverlay()),
          ],
        ),
      ),
    );
  }
}

class _ProcessingOverlay extends StatelessWidget {
  const _ProcessingOverlay();

  @override
  Widget build(BuildContext context) {
    final colors = context.editorColors;
    return ColoredBox(
      color: colors.imageStage.withValues(alpha: 0.38),
      child: Center(
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
          decoration: BoxDecoration(
            color: colors.imageStage.withValues(alpha: 0.94),
            borderRadius: BorderRadius.circular(AppRadii.small),
            border: Border.all(
              color: colors.onImageStage.withValues(alpha: 0.24),
            ),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              const SizedBox(
                width: 18,
                height: 18,
                child: CircularProgressIndicator(strokeWidth: 2),
              ),
              const SizedBox(width: 10),
              Text(
                context.l10n.processing,
                style: TextStyle(color: colors.onImageStage),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _ImageError extends StatelessWidget {
  const _ImageError();

  @override
  Widget build(BuildContext context) {
    final colors = context.editorColors;
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.broken_image_outlined, color: colors.imageStageMuted),
          const SizedBox(height: 8),
          Text(
            context.l10n.imageLoadFailed,
            style: TextStyle(color: colors.imageStageMuted),
          ),
        ],
      ),
    );
  }
}

class _ComparisonToggle extends StatelessWidget {
  const _ComparisonToggle({
    super.key,
    required this.value,
    required this.onChanged,
  });

  final ComparisonView value;
  final ValueChanged<ComparisonView> onChanged;

  @override
  Widget build(BuildContext context) {
    final colors = context.editorColors;
    final l10n = context.l10n;
    return DecoratedBox(
      decoration: BoxDecoration(
        color: colors.imageStage.withValues(alpha: 0.92),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: colors.onImageStage.withValues(alpha: 0.28)),
      ),
      child: Padding(
        padding: const EdgeInsets.all(3),
        child: Row(
          children: [
            Expanded(
              child: _ComparisonOption(
                key: const Key('comparison_mode_original'),
                label: l10n.labelOriginal,
                selected: value == ComparisonView.original,
                onTap: () => onChanged(ComparisonView.original),
              ),
            ),
            Expanded(
              child: _ComparisonOption(
                key: const Key('comparison_mode_compare'),
                label: l10n.labelCompare,
                selected: value == ComparisonView.compare,
                onTap: () => onChanged(ComparisonView.compare),
              ),
            ),
            Expanded(
              child: _ComparisonOption(
                key: const Key('comparison_mode_result'),
                label: l10n.labelResult,
                selected: value == ComparisonView.result,
                onTap: () => onChanged(ComparisonView.result),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _ComparisonOption extends StatelessWidget {
  const _ComparisonOption({
    super.key,
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final colors = context.editorColors;
    return Semantics(
      button: true,
      selected: selected,
      child: InkWell(
        borderRadius: BorderRadius.circular(999),
        onTap: onTap,
        child: ConstrainedBox(
          constraints: const BoxConstraints(minHeight: 44),
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 160),
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 9),
            decoration: BoxDecoration(
              color: selected ? colors.accent : Colors.transparent,
              borderRadius: BorderRadius.circular(999),
            ),
            alignment: Alignment.center,
            child: Text(
              label,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              textAlign: TextAlign.center,
              style: TextStyle(
                color: selected ? colors.onImageStage : colors.imageStageMuted,
                fontSize: 13,
                fontWeight: FontWeight.w700,
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _CurrentSummary extends StatelessWidget {
  const _CurrentSummary({required this.controller, required this.onTap});

  final EditorController controller;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final isPreview = controller.hasUncommittedPreview;
    final colors = context.editorColors;
    return Material(
      key: const Key('current_summary'),
      color: colors.surface,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppRadii.small),
        side: BorderSide(color: colors.border),
      ),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(AppRadii.small),
        child: Padding(
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.md,
            vertical: 12,
          ),
          child: Row(
            children: [
              Icon(
                isPreview ? Icons.pending_outlined : Icons.auto_fix_high,
                size: 19,
                color: isPreview ? colors.warning : colors.accentBright,
              ),
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                child: Text(
                  localizedCurrentSummary(context.l10n, controller),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    color: colors.textSecondary,
                    fontSize: 13,
                    fontWeight: FontWeight.w500,
                  ),
                ),
              ),
              if (controller.selectedEdit != null)
                Icon(Icons.chevron_right, color: colors.textMuted, size: 20),
            ],
          ),
        ),
      ),
    );
  }
}
