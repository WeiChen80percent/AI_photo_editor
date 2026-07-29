import 'edit_models.dart';
import 'editor_controller.dart';
import 'l10n/generated/app_localizations.dart';

bool _isEnglish(AppLocalizations l10n) =>
    l10n.localeName.toLowerCase().startsWith('en');

String localizedParameterLabel(
  AppLocalizations l10n,
  String key, {
  String? fallback,
}) {
  return switch (key.trim().toLowerCase()) {
    'exposure' => l10n.parameterExposure,
    'brightness' => l10n.parameterBrightness,
    'contrast' => l10n.parameterContrast,
    'highlights' => l10n.parameterHighlights,
    'shadows' => l10n.parameterShadows,
    'whites' => l10n.parameterWhites,
    'blacks' => l10n.parameterBlacks,
    'saturation' => l10n.parameterSaturation,
    'vibrance' => l10n.parameterVibrance,
    'temperature' => l10n.parameterTemperature,
    'white_balance_tint' => l10n.parameterWhiteBalanceTint,
    'sharpen' => l10n.parameterSharpen,
    'clarity' => l10n.parameterClarity,
    'dehaze' => l10n.parameterDehaze,
    'vignette' => l10n.parameterVignette,
    _ =>
      !_isEnglish(l10n) && (fallback?.trim().isNotEmpty ?? false)
          ? fallback!.trim()
          : key,
  };
}

String localizedRegionLabel(AppLocalizations l10n, String region) {
  return switch (region.trim().toLowerCase()) {
    'all' => l10n.regionAll,
    'sky' => l10n.regionSky,
    'person' => l10n.regionPerson,
    'background' => l10n.regionBackground,
    'highlights' => l10n.regionHighlights,
    'shadows' => l10n.regionShadows,
    'center' => l10n.regionCenter,
    'edges' => l10n.regionEdges,
    _ => region,
  };
}

String localizedStyleFamilyLabel(AppLocalizations l10n, String family) {
  return switch (family.trim().toLowerCase()) {
    'natural_clean' => l10n.styleFamilyNaturalClean,
    'portrait_skin' => l10n.styleFamilyPortraitSkin,
    'landscape_travel' => l10n.styleFamilyLandscapeTravel,
    'cinematic' => l10n.styleFamilyCinematic,
    'film_retro' => l10n.styleFamilyFilmRetro,
    'black_white' => l10n.styleFamilyBlackWhite,
    'night_neon' => l10n.styleFamilyNightNeon,
    'pastel_creative' => l10n.styleFamilyPastelCreative,
    _ => family,
  };
}

String localizedParserSourceLabel(AppLocalizations l10n, String? source) {
  return switch (source?.trim().toLowerCase()) {
    'llm' => l10n.parserLlm,
    'rule_based_fallback' => l10n.parserRules,
    'reference_mode' => l10n.parserReference,
    'manual_parameters' => l10n.parserManual,
    'semantic_registry' => l10n.parserRules,
    'adaptive_v2_deterministic' => l10n.parserRules,
    'style_catalog' => l10n.parserRules,
    null || '' => '',
    final value => value,
  };
}

String localizedStyleName(
  AppLocalizations l10n, {
  required String zh,
  required String en,
  required String fallback,
}) {
  final preferred = _isEnglish(l10n) ? en.trim() : zh.trim();
  if (preferred.isNotEmpty) {
    return preferred;
  }
  if (!_isEnglish(l10n) && en.trim().isNotEmpty) {
    return en.trim();
  }
  return fallback;
}

String localizedCatalogStyleName(
  AppLocalizations l10n,
  StyleCatalogItem style,
) {
  return localizedStyleName(
    l10n,
    zh: style.displayNameZh,
    en: style.displayNameEn,
    fallback: style.styleId,
  );
}

String localizedHistoryStyleName(AppLocalizations l10n, StyleMetadata style) {
  return localizedStyleName(
    l10n,
    zh: style.displayNameZh,
    en: style.displayNameEn,
    fallback: style.styleId,
  );
}

String localizedEditModeLabel(AppLocalizations l10n, EditHistoryItem edit) {
  if (edit.resolvedIntent == 'apply_style') {
    return l10n.modeStyle;
  }
  return switch (edit.editMode) {
    'manual' || 'manual_preview' => l10n.modeManual,
    'reference' => l10n.modeReference,
    _ => l10n.modePrompt,
  };
}

String localizedEditDisplayTitle(AppLocalizations l10n, EditHistoryItem edit) {
  final style = edit.style;
  if (edit.isDirectStyleEdit && style != null) {
    return '${localizedHistoryStyleName(l10n, style)} · '
        '${(style.strength * 100).round()}%';
  }
  if (edit.editMode == 'manual') {
    return l10n.manualEditDisplayTitle;
  }
  if (edit.editMode == 'reference') {
    return l10n.referenceEditDisplayTitle;
  }
  return edit.prompt.isEmpty ? l10n.promptEditFallbackTitle : edit.prompt;
}

String localizedCompactParameterSummary(
  AppLocalizations l10n,
  Map<String, dynamic> parameters, {
  int limit = 3,
  ParameterMetadataCatalog? metadataCatalog,
}) {
  final catalog = metadataCatalog ?? ParameterMetadataCatalog.legacy;
  final entries = <String>[];
  for (final entry in parameters.entries) {
    final metadata = catalog.metadataFor(entry.key);
    final value = entry.value;
    if (metadata == null || value is! num || !metadata.isMeaningful(value)) {
      continue;
    }
    final normalized = value.toDouble();
    final formatted = metadata.formatValue(
      normalized,
      signed: metadata.neutral == 0,
    );
    entries.add(
      '${localizedParameterLabel(l10n, entry.key, fallback: metadata.label)} '
      '$formatted',
    );
    if (entries.length == limit) {
      break;
    }
  }
  return entries.join(' · ');
}

String localizedCurrentSummary(
  AppLocalizations l10n,
  EditorController controller,
) {
  final edit = controller.selectedEdit;
  if (edit == null) {
    return controller.isOriginalBaseSelected
        ? l10n.summaryOriginalNewBranch
        : l10n.summaryChoosePhoto;
  }

  final target = localizedRegionLabel(
    l10n,
    (controller.currentParameters['region'] ?? edit.region).toString(),
  );
  final displayParameters = controller.hasUncommittedPreview
      ? controller.currentParameters
      : edit.parametersForDisplay(controller.parameterMetadataCatalog);
  final parameters = localizedCompactParameterSummary(
    l10n,
    displayParameters,
    metadataCatalog: controller.parameterMetadataCatalog,
  );
  final previewLabel = controller.hasUncommittedPreview
      ? l10n.summaryPreviewPrefix
      : '';
  final style = edit.style;
  final styleLabel = style == null
      ? ''
      : '${localizedHistoryStyleName(l10n, style)} '
            '${(style.strength * 100).round()}% · ';
  if (parameters.isEmpty) {
    return '$previewLabel$styleLabel$target · '
        '${localizedEditModeLabel(l10n, edit)}';
  }
  final parameterLabel =
      edit.isDirectStyleEdit && !controller.hasUncommittedPreview
      ? l10n.equivalentParameters(parameters)
      : parameters;
  return '$previewLabel$styleLabel$target · $parameterLabel';
}

String localizedAdaptiveReasonLabel(AppLocalizations l10n, String? reason) {
  return switch (reason?.trim().toLowerCase()) {
    'initial_adjustment' ||
    'initial' ||
    'initial_step' ||
    'initial_anchor_step' ||
    'initial_template' ||
    'initial_negative_bracket' => l10n.adaptiveReasonInitial,
    'continue_same_direction' ||
    'continue' ||
    'unbounded_same_direction' ||
    'same_direction_unbounded' ||
    'unbounded_template_step' => l10n.adaptiveReasonContinue,
    'reverse_direction' ||
    'reverse' ||
    'direction_reversal' => l10n.adaptiveReasonReverse,
    'narrow_interval' ||
    'narrow' ||
    'companion_takeover' => l10n.adaptiveReasonNarrow,
    'midpoint' ||
    'binary_search_midpoint' ||
    'bracket_midpoint' ||
    'bounded_midpoint' => l10n.adaptiveReasonMidpoint,
    'relative_numeric' ||
    'relative_value' ||
    'relative_numeric_reset' => l10n.adaptiveReasonRelative,
    'absolute_numeric' ||
    'absolute_value' ||
    'absolute_value_reset' => l10n.adaptiveReasonAbsolute,
    'reset_axis' ||
    'axis_reset' ||
    'explicit_strength_reset' => l10n.adaptiveReasonResetAxis,
    'reset_original' || 'global_reset' => l10n.adaptiveReasonResetOriginal,
    'interval_reset' || 'state_reset' => l10n.adaptiveReasonReanchor,
    'handoff' => l10n.adaptiveReasonHandoff,
    null || '' => '',
    final value => value,
  };
}

String localizedPresentationMessage(
  AppLocalizations l10n,
  EditorPresentationMessage? message, {
  String? legacyFallback,
}) {
  if (message == null) {
    if (!_isEnglish(l10n) && legacyFallback != null) {
      return legacyFallback;
    }
    return l10n.backendInvalidResponse;
  }

  final arguments = message.arguments;
  final error = (arguments['error'] ?? '').toString();
  final statusCode = arguments['statusCode'];
  final base = switch (message.code) {
    'status_selected_new_original' => l10n.statusSelectedNewOriginal,
    'status_reference_ready' => l10n.statusReferenceReady,
    'prompt_required' => l10n.errorPromptRequired,
    'reference_required' => l10n.errorReferenceRequired,
    'original_required' => l10n.errorOriginalRequired,
    'parsing_prompt' => l10n.statusParsingPrompt,
    'applying_reference' => l10n.statusApplyingReference,
    'edit_complete' => l10n.statusEditComplete,
    'history_synced' => l10n.statusHistorySynced,
    'switched_history' => l10n.statusSwitchedHistory,
    'switched_original' => l10n.statusSwitchedOriginal,
    'manual_need_prompt' => l10n.manualUnavailableNeedPrompt,
    'manual_reference_unsupported' => l10n.manualUnavailableReference,
    'manual_engine_unsupported' => l10n.manualUnavailableEngine,
    'manual_unavailable' => l10n.manualUnavailableGeneric,
    'reset_source_parameters' => l10n.statusResetSourceParameters,
    'applying_manual' => l10n.statusApplyingManual,
    'manual_committed' => l10n.statusManualCommitted,
    'style_catalog_load_failed' => l10n.errorStyleCatalogLoad(error),
    'edit_failed' => l10n.errorEditFailed(error),
    'open_manual_failed' => l10n.errorOpenManual(error),
    'manual_preview_failed' => l10n.errorManualPreview(error),
    'manual_commit_failed' => l10n.errorManualCommit(error),
    'style_selection_ambiguous' => l10n.errorStyleAmbiguous,
    'style_compound_not_supported' => l10n.errorStyleCompound,
    'style_asset_invalid' || 'style_version_mismatch' => l10n.errorStyleAsset,
    'semantic_target_not_found' => l10n.errorSemanticTargetNotFound,
    'adaptive_clarification_required' => l10n.errorAdaptiveClarification,
    'adaptive_step_converged' => l10n.errorAdaptiveConverged,
    'adaptive_feedback_satisfied' => l10n.errorAdaptiveSatisfied,
    'manual_source_mode_unsupported' => l10n.errorManualSourceUnsupported,
    'network_error' => l10n.errorBackendUnavailable,
    'invalid_response' => l10n.backendInvalidResponse,
    _ when statusCode is int => l10n.backendHttpError(statusCode),
    _ =>
      _isEnglish(l10n)
          ? l10n.backendInvalidResponse
          : (legacyFallback ?? l10n.backendInvalidResponse),
  };

  final rawIssues = message.details['issues'];
  if (rawIssues is! List) {
    return base;
  }
  final contexts = <String>[];
  for (final value in rawIssues) {
    if (value is! Map) {
      continue;
    }
    final issue = Map<String, dynamic>.from(value);
    final axis = issue['axis']?.toString().trim();
    final region = issue['region']?.toString().trim();
    final sourceClause = issue['source_clause']?.toString().trim();
    final parts = <String>[
      if (axis != null && axis.isNotEmpty) localizedParameterLabel(l10n, axis),
      if (region != null && region.isNotEmpty)
        localizedRegionLabel(l10n, region),
      if (sourceClause != null && sourceClause.isNotEmpty) '“$sourceClause”',
    ];
    final context = parts.join(' ');
    if (context.isNotEmpty && !contexts.contains(context)) {
      contexts.add(context);
    }
  }
  return contexts.isEmpty
      ? base
      : l10n.adaptiveIssuesContext(base, contexts.join(', '));
}
