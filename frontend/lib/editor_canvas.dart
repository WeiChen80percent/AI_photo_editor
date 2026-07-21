import 'dart:typed_data';

import 'package:flutter/material.dart';

import 'app_theme.dart';
import 'editor_controller.dart';

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
  bool _holdingOriginal = false;

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
    return Row(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Expanded(
          child: _ImageStage(
            key: const Key('original_image'),
            label: '原圖',
            bytes: controller.originalImageBytes,
            imageUrl: controller.originalImageUrl,
          ),
        ),
        const SizedBox(width: AppSpacing.sm),
        Expanded(
          child: _ImageStage(
            key: const Key('result_image'),
            label: controller.hasUncommittedPreview ? '預覽' : '結果',
            imageUrl: controller.currentResultUrl,
            emptyLabel: '完成修圖後，結果會顯示在這裡',
            isProcessing: controller.isProcessing || controller.isPreviewing,
          ),
        ),
      ],
    );
  }

  Widget _buildSingleCanvas(EditorController controller) {
    final showOriginal =
        _holdingOriginal ||
        controller.comparisonView == ComparisonView.original ||
        controller.currentResultUrl == null;
    return Stack(
      children: [
        Positioned.fill(
          child: GestureDetector(
            onLongPressStart: controller.currentResultUrl == null
                ? null
                : (_) => setState(() => _holdingOriginal = true),
            onLongPressEnd: controller.currentResultUrl == null
                ? null
                : (_) => setState(() => _holdingOriginal = false),
            child: _ImageStage(
              key: Key(showOriginal ? 'original_image' : 'result_image'),
              label: showOriginal
                  ? '原圖'
                  : controller.hasUncommittedPreview
                  ? '預覽'
                  : '結果',
              bytes: showOriginal ? controller.originalImageBytes : null,
              imageUrl: showOriginal
                  ? controller.originalImageUrl
                  : controller.currentResultUrl,
              emptyLabel: '完成修圖後，結果會顯示在這裡',
              isProcessing:
                  !showOriginal &&
                  (controller.isProcessing || controller.isPreviewing),
            ),
          ),
        ),
        if (controller.currentResultUrl != null)
          Positioned(
            left: 0,
            right: 0,
            bottom: AppSpacing.md,
            child: Center(
              child: _ComparisonToggle(
                key: const Key('comparison_toggle'),
                value: showOriginal
                    ? ComparisonView.original
                    : ComparisonView.result,
                onChanged: controller.setComparisonView,
              ),
            ),
          ),
      ],
    );
  }
}

class _EmptyCanvas extends StatelessWidget {
  const _EmptyCanvas({required this.onPickOriginal});

  final VoidCallback onPickOriginal;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        color: AppColors.imageStage,
        borderRadius: BorderRadius.circular(AppRadii.medium),
        border: Border.all(color: AppColors.border),
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
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.xl),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            _emptyIcon(size: 72, iconSize: 32),
            const SizedBox(height: AppSpacing.md),
            Text('選擇一張照片開始', style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: AppSpacing.xs),
            const Text(
              '照片會完整顯示，修圖結果與歷史版本都會保留。',
              textAlign: TextAlign.center,
              style: TextStyle(color: AppColors.textSecondary),
            ),
            const SizedBox(height: AppSpacing.lg),
            _pickButton(),
          ],
        ),
      ),
    );
  }

  Widget _buildShortLandscape(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.sm,
      ),
      child: Row(
        children: [
          _emptyIcon(size: 52, iconSize: 25),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '選擇一張照片開始',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                const SizedBox(height: 3),
                const Text(
                  '照片、結果與歷史版本都會保留。',
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    color: AppColors.textSecondary,
                    fontSize: 12,
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(width: AppSpacing.md),
          _pickButton(),
        ],
      ),
    );
  }

  Widget _emptyIcon({required double size, required double iconSize}) {
    return Container(
      width: size,
      height: size,
      decoration: const BoxDecoration(
        color: AppColors.surfaceRaised,
        shape: BoxShape.circle,
      ),
      child: Icon(
        Icons.add_photo_alternate_outlined,
        size: iconSize,
        color: AppColors.textSecondary,
      ),
    );
  }

  Widget _pickButton() {
    return FilledButton.icon(
      key: const Key('pick_original_button'),
      onPressed: onPickOriginal,
      icon: const Icon(Icons.photo_library_outlined),
      label: const Text('選擇原圖'),
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
    return ClipRRect(
      borderRadius: BorderRadius.circular(AppRadii.medium),
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: AppColors.imageStage,
          border: Border.all(color: AppColors.border),
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
                    emptyLabel ?? '尚無圖片',
                    textAlign: TextAlign.center,
                    style: const TextStyle(color: AppColors.textMuted),
                  ),
                ),
              ),
            Positioned(
              left: AppSpacing.sm,
              top: AppSpacing.sm,
              child: DecoratedBox(
                decoration: BoxDecoration(
                  color: const Color(0xD914161B),
                  borderRadius: BorderRadius.circular(AppRadii.small),
                  border: Border.all(color: const Color(0x443F4450)),
                ),
                child: Padding(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 11,
                    vertical: 7,
                  ),
                  child: Text(
                    label,
                    style: const TextStyle(
                      color: AppColors.textPrimary,
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
    return ColoredBox(
      color: Color(0x44000000),
      child: Center(
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
          decoration: BoxDecoration(
            color: const Color(0xE61B1E24),
            borderRadius: BorderRadius.circular(AppRadii.small),
            border: Border.all(color: AppColors.border),
          ),
          child: const Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              SizedBox(
                width: 18,
                height: 18,
                child: CircularProgressIndicator(strokeWidth: 2),
              ),
              SizedBox(width: 10),
              Text('處理中…'),
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
    return const Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.broken_image_outlined, color: AppColors.textMuted),
          SizedBox(height: 8),
          Text('圖片載入失敗', style: TextStyle(color: AppColors.textSecondary)),
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
    return DecoratedBox(
      decoration: BoxDecoration(
        color: const Color(0xE817191E),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: AppColors.borderStrong),
      ),
      child: Padding(
        padding: const EdgeInsets.all(3),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            _ComparisonOption(
              label: '原圖',
              selected: value == ComparisonView.original,
              onTap: () => onChanged(ComparisonView.original),
            ),
            _ComparisonOption(
              label: '結果',
              selected: value == ComparisonView.result,
              onTap: () => onChanged(ComparisonView.result),
            ),
          ],
        ),
      ),
    );
  }
}

class _ComparisonOption extends StatelessWidget {
  const _ComparisonOption({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      button: true,
      selected: selected,
      child: InkWell(
        borderRadius: BorderRadius.circular(999),
        onTap: onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 160),
          padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 9),
          decoration: BoxDecoration(
            color: selected ? AppColors.accent : Colors.transparent,
            borderRadius: BorderRadius.circular(999),
          ),
          child: Text(
            label,
            style: TextStyle(
              color: selected ? AppColors.textPrimary : AppColors.textSecondary,
              fontSize: 13,
              fontWeight: FontWeight.w700,
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
    return Material(
      key: const Key('current_summary'),
      color: AppColors.surface,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppRadii.small),
        side: const BorderSide(color: AppColors.border),
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
                color: isPreview ? AppColors.warning : AppColors.accentBright,
              ),
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                child: Text(
                  controller.currentSummary,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    color: AppColors.textSecondary,
                    fontSize: 13,
                    fontWeight: FontWeight.w500,
                  ),
                ),
              ),
              if (controller.selectedEdit != null)
                const Icon(
                  Icons.chevron_right,
                  color: AppColors.textMuted,
                  size: 20,
                ),
            ],
          ),
        ),
      ),
    );
  }
}
