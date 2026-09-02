typedef ImageUrlBuilder = String Function(String path);

Map<String, dynamic> _mapValue(dynamic value) {
  if (value is Map) {
    return Map<String, dynamic>.from(value);
  }
  return <String, dynamic>{};
}

String? _stringValue(dynamic value) {
  final text = value?.toString().trim();
  return text == null || text.isEmpty ? null : text;
}

List<String> _stringList(dynamic value) {
  if (value is! List) {
    return const <String>[];
  }
  return value.map(_stringValue).whereType<String>().toList(growable: false);
}

double _doubleValue(dynamic value, {double fallback = 0}) {
  if (value is num) {
    return value.toDouble();
  }
  return double.tryParse(value?.toString() ?? '') ?? fallback;
}

double? _nullableDoubleValue(dynamic value) {
  if (value is num) {
    return value.toDouble();
  }
  return double.tryParse(value?.toString() ?? '');
}

bool? _nullableBoolValue(dynamic value) {
  if (value is bool) {
    return value;
  }
  switch (value?.toString().trim().toLowerCase()) {
    case 'true':
    case '1':
      return true;
    case 'false':
    case '0':
      return false;
    default:
      return null;
  }
}

int? _nullableIntValue(dynamic value) {
  if (value is num) {
    return value.toInt();
  }
  return int.tryParse(value?.toString() ?? '');
}

String _formatParameterNumber(double value, {bool signed = false}) {
  final rounded = value == value.roundToDouble()
      ? value.toInt().toString()
      : value
            .toStringAsFixed(3)
            .replaceFirst(RegExp(r'0+$'), '')
            .replaceFirst(RegExp(r'\.$'), '');
  return signed && value > 0 ? '+$rounded' : rounded;
}

class ParameterMetadata {
  const ParameterMetadata({
    required this.key,
    required this.label,
    required this.unit,
    required this.neutral,
    required this.transform,
    this.minimum,
    this.maximum,
    this.quantum,
  });

  final String key;
  final String label;
  final String unit;
  final double neutral;
  final String transform;
  final double? minimum;
  final double? maximum;
  final double? quantum;

  bool get usesLogTransform => transform.toLowerCase() == 'log';

  bool isMeaningful(num value) {
    return (value.toDouble() - neutral).abs() > 0.0001;
  }

  String formatValue(double value, {bool signed = false}) {
    final number = _formatParameterNumber(value, signed: signed);
    if (unit.isEmpty) {
      return number;
    }
    return unit == 'x' ? '$number$unit' : '$number $unit';
  }

  String formatStep(double value) {
    if (usesLogTransform) {
      return _formatParameterNumber(value);
    }
    return formatValue(value);
  }
}

const Map<String, ParameterMetadata> _legacyParameterMetadata =
    <String, ParameterMetadata>{
      'exposure': ParameterMetadata(
        key: 'exposure',
        label: '曝光',
        unit: 'EV',
        neutral: 0,
        transform: 'linear',
      ),
      'brightness': ParameterMetadata(
        key: 'brightness',
        label: '亮度',
        unit: '',
        neutral: 0,
        transform: 'linear',
      ),
      'contrast': ParameterMetadata(
        key: 'contrast',
        label: '對比',
        unit: 'x',
        neutral: 1,
        transform: 'log',
      ),
      'highlights': ParameterMetadata(
        key: 'highlights',
        label: '高光',
        unit: '',
        neutral: 0,
        transform: 'linear',
      ),
      'shadows': ParameterMetadata(
        key: 'shadows',
        label: '陰影',
        unit: '',
        neutral: 0,
        transform: 'linear',
      ),
      'whites': ParameterMetadata(
        key: 'whites',
        label: '白位',
        unit: '',
        neutral: 0,
        transform: 'linear',
      ),
      'blacks': ParameterMetadata(
        key: 'blacks',
        label: '黑位',
        unit: '',
        neutral: 0,
        transform: 'linear',
      ),
      'saturation': ParameterMetadata(
        key: 'saturation',
        label: '飽和度',
        unit: 'x',
        neutral: 1,
        transform: 'log',
      ),
      'vibrance': ParameterMetadata(
        key: 'vibrance',
        label: '自然飽和度',
        unit: '',
        neutral: 0,
        transform: 'linear',
      ),
      'temperature': ParameterMetadata(
        key: 'temperature',
        label: '色溫',
        unit: '',
        neutral: 0,
        transform: 'linear',
      ),
      'white_balance_tint': ParameterMetadata(
        key: 'white_balance_tint',
        label: '白平衡色偏',
        unit: '',
        neutral: 0,
        transform: 'linear',
      ),
      'sharpen': ParameterMetadata(
        key: 'sharpen',
        label: '銳化',
        unit: '',
        neutral: 0,
        transform: 'linear',
      ),
      'clarity': ParameterMetadata(
        key: 'clarity',
        label: '清晰度',
        unit: '',
        neutral: 0,
        transform: 'linear',
      ),
      'dehaze': ParameterMetadata(
        key: 'dehaze',
        label: '去霧',
        unit: '',
        neutral: 0,
        transform: 'linear',
      ),
      'vignette': ParameterMetadata(
        key: 'vignette',
        label: '暗角',
        unit: '',
        neutral: 0,
        transform: 'linear',
      ),
    };

class ParameterMetadataCatalog {
  ParameterMetadataCatalog._(Map<String, ParameterMetadata> metadata)
    : _metadata = Map<String, ParameterMetadata>.unmodifiable(metadata);

  static final ParameterMetadataCatalog legacy = ParameterMetadataCatalog._(
    _legacyParameterMetadata,
  );

  factory ParameterMetadataCatalog.fromSources({
    ManualSchema? manualSchema,
    dynamic policyRegistry,
    bool includeLegacyFallback = true,
  }) {
    final merged = <String, ParameterMetadata>{
      if (includeLegacyFallback) ..._legacyParameterMetadata,
    };
    final registry = _mapValue(policyRegistry);
    for (final entry in registry.entries) {
      if (entry.value is! Map) {
        continue;
      }
      final policy = Map<String, dynamic>.from(entry.value as Map);
      final key = entry.key.trim().toLowerCase();
      final axis = (_stringValue(policy['axis']) ?? key).toLowerCase();
      if (key.isEmpty || axis != key) {
        continue;
      }
      final existing = merged[key];
      merged[key] = ParameterMetadata(
        key: key,
        label: _stringValue(policy['label']) ?? existing?.label ?? key,
        unit: _stringValue(policy['unit']) ?? existing?.unit ?? '',
        neutral:
            _nullableDoubleValue(policy['neutral']) ?? existing?.neutral ?? 0,
        transform:
            _stringValue(policy['transform']) ??
            existing?.transform ??
            'linear',
        minimum: _nullableDoubleValue(policy['minimum']) ?? existing?.minimum,
        maximum: _nullableDoubleValue(policy['maximum']) ?? existing?.maximum,
        quantum: _nullableDoubleValue(policy['quantum']) ?? existing?.quantum,
      );
    }
    for (final spec
        in manualSchema?.parameters ?? const <ManualParameterSpec>[]) {
      final key = spec.key.trim().toLowerCase();
      if (key.isEmpty) {
        continue;
      }
      final existing = merged[key];
      merged[key] = ParameterMetadata(
        key: key,
        label: spec.label,
        unit: spec.unit,
        neutral: spec.neutral,
        transform: existing?.transform ?? 'linear',
        minimum: spec.minimum,
        maximum: spec.maximum,
        quantum: spec.step,
      );
    }
    return ParameterMetadataCatalog._(merged);
  }

  final Map<String, ParameterMetadata> _metadata;

  ParameterMetadata? metadataFor(String key) {
    return _metadata[key.trim().toLowerCase()];
  }

  bool contains(String key) => metadataFor(key) != null;

  String labelFor(String key) => metadataFor(key)?.label ?? key;
}

class AdaptiveOperation {
  const AdaptiveOperation({
    required this.axis,
    required this.region,
    required this.relation,
    required this.reason,
    required this.currentValue,
    required this.nextValue,
    required this.deltaFromParent,
    required this.lowerBound,
    required this.upperBound,
    required this.stepBefore,
    required this.stepAfter,
    required this.direction,
    required this.applied,
    required this.converged,
    this.operationId,
    this.groupId,
  });

  final String? operationId;
  final String? groupId;
  final String axis;
  final String region;
  final String? relation;
  final String? reason;
  final double? currentValue;
  final double? nextValue;
  final double? deltaFromParent;
  final double? lowerBound;
  final double? upperBound;
  final double? stepBefore;
  final double? stepAfter;
  final int? direction;
  final bool? applied;
  final bool? converged;

  static AdaptiveOperation? tryParse(
    dynamic value, {
    String defaultRegion = 'all',
    ParameterMetadataCatalog? metadataCatalog,
  }) {
    if (value is! Map) {
      return null;
    }
    final json = Map<String, dynamic>.from(value);
    final axis = _stringValue(json['axis'] ?? json['primary_parameter']);
    final catalog = metadataCatalog ?? ParameterMetadataCatalog.legacy;
    if (axis == null || !catalog.contains(axis)) {
      return null;
    }
    final hasMeaningfulState = <dynamic>[
      json['relation'],
      json['reason'],
      json['current_value'],
      json['next_value'],
      json['delta_from_parent'],
      json['lower_bound'],
      json['upper_bound'],
      json['step_before'],
      json['step_after'],
      json['direction'],
      json['applied'],
      json['converged'],
      json['bounds_before'],
      json['bounds_after'],
    ].any((field) => field != null);
    if (!hasMeaningfulState) {
      return null;
    }
    final boundsBefore = _mapValue(json['bounds_before']);
    final boundsAfter = _mapValue(json['bounds_after']);
    return AdaptiveOperation(
      operationId: _stringValue(json['operation_id']),
      groupId: _stringValue(json['group_id']),
      axis: axis,
      region: _stringValue(json['region']) ?? defaultRegion,
      relation: _stringValue(json['relation']),
      reason: _stringValue(json['reason']),
      currentValue: _nullableDoubleValue(json['current_value']),
      nextValue: _nullableDoubleValue(json['next_value']),
      deltaFromParent: _nullableDoubleValue(json['delta_from_parent']),
      lowerBound: _nullableDoubleValue(
        json['lower_bound'] ?? boundsAfter['lower'] ?? boundsBefore['lower'],
      ),
      upperBound: _nullableDoubleValue(
        json['upper_bound'] ?? boundsAfter['upper'] ?? boundsBefore['upper'],
      ),
      stepBefore: _nullableDoubleValue(json['step_before']),
      stepAfter: _nullableDoubleValue(json['step_after']),
      direction: _nullableIntValue(json['direction']),
      applied: _nullableBoolValue(json['applied']),
      converged: _nullableBoolValue(json['converged']),
    );
  }
}

class ManualParameterSpec {
  const ManualParameterSpec({
    required this.key,
    required this.label,
    required this.group,
    required this.minimum,
    required this.maximum,
    required this.step,
    required this.neutral,
    required this.unit,
    required this.defaultVisible,
    required this.order,
  });

  final String key;
  final String label;
  final String group;
  final double minimum;
  final double maximum;
  final double step;
  final double neutral;
  final String unit;
  final bool defaultVisible;
  final int order;

  factory ManualParameterSpec.fromJson(Map<String, dynamic> json) {
    return ManualParameterSpec(
      key: json['key'] as String,
      label: (json['label'] as String?) ?? json['key'] as String,
      group: (json['group'] as String?) ?? 'other',
      minimum: _doubleValue(json['minimum']),
      maximum: _doubleValue(json['maximum']),
      step: _doubleValue(json['step'], fallback: 1),
      neutral: _doubleValue(json['neutral']),
      unit: (json['unit'] as String?) ?? '',
      defaultVisible: json['default_visible'] == true,
      order: (json['order'] as num?)?.toInt() ?? 0,
    );
  }

  double normalize(double value) {
    final clamped = value.clamp(minimum, maximum).toDouble();
    final steps = ((clamped - minimum) / step).round();
    final normalized = minimum + steps * step;
    return double.parse(normalized.toStringAsFixed(decimalPlaces));
  }

  int get decimalPlaces {
    final text = step.toStringAsFixed(8).replaceFirst(RegExp(r'0+$'), '');
    final decimal = text.indexOf('.');
    return decimal < 0 ? 0 : text.length - decimal - 1;
  }

  String format(double value) {
    final normalized = normalize(value);
    final number = normalized.toStringAsFixed(decimalPlaces);
    final prefix = normalized > 0 && neutral == 0 ? '+' : '';
    return '$prefix$number${unit.isEmpty ? '' : ' $unit'}';
  }
}

class ManualSchema {
  const ManualSchema({
    required this.version,
    required this.engine,
    required this.parameters,
  });

  final String version;
  final String engine;
  final List<ManualParameterSpec> parameters;

  factory ManualSchema.fromJson(Map<String, dynamic> json) {
    final rawParameters = json['parameters'];
    final parameters = rawParameters is List
        ? rawParameters
              .whereType<Map>()
              .map(
                (item) => ManualParameterSpec.fromJson(
                  Map<String, dynamic>.from(item),
                ),
              )
              .toList()
        : <ManualParameterSpec>[];
    parameters.sort((a, b) => a.order.compareTo(b.order));
    return ManualSchema(
      version: (json['schema_version'] as String?) ?? 'unknown',
      engine: (json['engine'] as String?) ?? 'opencv',
      parameters: parameters,
    );
  }

  ManualParameterSpec? byKey(String key) {
    for (final parameter in parameters) {
      if (parameter.key == key) {
        return parameter;
      }
    }
    return null;
  }
}

class StyleMetadata {
  const StyleMetadata({
    required this.styleId,
    required this.version,
    required this.strength,
    required this.family,
    required this.displayNameZh,
    required this.displayNameEn,
    required this.recipeHash,
    required this.assetHash,
    required this.rendererVersion,
    required this.reviewStatus,
    required this.sourceEditId,
    required this.anchorImagePath,
  });

  final String styleId;
  final String version;
  final double strength;
  final String family;
  final String displayNameZh;
  final String displayNameEn;
  final String recipeHash;
  final String assetHash;
  final String rendererVersion;
  final String reviewStatus;
  final String? sourceEditId;
  final String? anchorImagePath;

  String get displayName =>
      displayNameZh.isNotEmpty ? displayNameZh : displayNameEn;

  factory StyleMetadata.fromJson(Map<String, dynamic> json) {
    final displayName = _mapValue(json['display_name']);
    return StyleMetadata(
      styleId: _stringValue(json['style_id']) ?? '',
      version: _stringValue(json['version']) ?? '',
      strength: _nullableDoubleValue(json['strength']) ?? 1,
      family: _stringValue(json['family']) ?? '',
      displayNameZh: _stringValue(displayName['zh']) ?? '',
      displayNameEn: _stringValue(displayName['en']) ?? '',
      recipeHash: _stringValue(json['recipe_hash']) ?? '',
      assetHash: _stringValue(json['asset_hash']) ?? '',
      rendererVersion: _stringValue(json['renderer_version']) ?? '',
      reviewStatus: _stringValue(json['review_status']) ?? '',
      sourceEditId: _stringValue(json['source_edit_id']),
      anchorImagePath: _stringValue(json['anchor_image_path']),
    );
  }
}

class StyleCatalogItem {
  const StyleCatalogItem({
    required this.styleId,
    required this.version,
    required this.displayNameZh,
    required this.displayNameEn,
    required this.family,
    required this.tags,
    required this.description,
    required this.defaultStrength,
    required this.minimumStrength,
    required this.maximumStrength,
    required this.previewUrl,
  });

  final String styleId;
  final String version;
  final String displayNameZh;
  final String displayNameEn;
  final String family;
  final List<String> tags;
  final String description;
  final double defaultStrength;
  final double minimumStrength;
  final double maximumStrength;
  final String? previewUrl;

  String get displayName =>
      displayNameZh.isNotEmpty ? displayNameZh : displayNameEn;

  factory StyleCatalogItem.fromJson(
    Map<String, dynamic> json, {
    required ImageUrlBuilder buildImageUrl,
  }) {
    final displayName = _mapValue(json['display_name']);
    final strength = _mapValue(json['strength']);
    final review = _mapValue(json['review']);
    final previewPath = _stringValue(review['preview_path']);
    return StyleCatalogItem(
      styleId: _stringValue(json['style_id']) ?? '',
      version: _stringValue(json['version']) ?? '',
      displayNameZh: _stringValue(displayName['zh']) ?? '',
      displayNameEn: _stringValue(displayName['en']) ?? '',
      family: _stringValue(json['family']) ?? '',
      tags: _stringList(json['tags']),
      description: _stringValue(json['description']) ?? '',
      defaultStrength: _nullableDoubleValue(strength['default']) ?? 1,
      minimumStrength: _nullableDoubleValue(strength['minimum']) ?? 0,
      maximumStrength: _nullableDoubleValue(strength['maximum']) ?? 1,
      previewUrl: previewPath == null ? null : buildImageUrl(previewPath),
    );
  }
}

class StyleCatalog {
  const StyleCatalog({
    required this.catalogVersion,
    required this.styleCount,
    required this.families,
    required this.styles,
  });

  final String catalogVersion;
  final int styleCount;
  final Map<String, int> families;
  final List<StyleCatalogItem> styles;

  factory StyleCatalog.fromJson(
    Map<String, dynamic> json, {
    required ImageUrlBuilder buildImageUrl,
  }) {
    final familyMap = _mapValue(json['families']);
    final styles = json['styles'];
    return StyleCatalog(
      catalogVersion: _stringValue(json['catalog_version']) ?? '',
      styleCount: (json['style_count'] as num?)?.toInt() ?? 0,
      families: <String, int>{
        for (final entry in familyMap.entries)
          if (entry.value is num) entry.key: (entry.value as num).toInt(),
      },
      styles: styles is List
          ? styles
                .whereType<Map>()
                .map(
                  (item) => StyleCatalogItem.fromJson(
                    Map<String, dynamic>.from(item),
                    buildImageUrl: buildImageUrl,
                  ),
                )
                .toList(growable: false)
          : const <StyleCatalogItem>[],
    );
  }
}

class CommandLocalizedText {
  const CommandLocalizedText({required this.zh, required this.en});

  final String zh;
  final String en;

  String forLanguage(String languageCode) =>
      languageCode.toLowerCase() == 'en' ? en : zh;

  factory CommandLocalizedText.fromJson(dynamic value) {
    final json = _mapValue(value);
    return CommandLocalizedText(
      zh: _stringValue(json['zh']) ?? '',
      en: _stringValue(json['en']) ?? '',
    );
  }
}

class CommandClarificationOption {
  const CommandClarificationOption({
    required this.optionId,
    required this.label,
    this.action = const <String, dynamic>{},
  });

  final String optionId;
  final CommandLocalizedText label;
  final Map<String, dynamic> action;

  factory CommandClarificationOption.fromJson(Map<String, dynamic> json) {
    return CommandClarificationOption(
      optionId: _stringValue(json['option_id']) ?? '',
      label: CommandLocalizedText.fromJson(json['label']),
      action: _mapValue(json['action']),
    );
  }
}

class CommandClarification {
  const CommandClarification({
    required this.code,
    required this.question,
    this.options = const <CommandClarificationOption>[],
  });

  final String code;
  final CommandLocalizedText question;
  final List<CommandClarificationOption> options;

  factory CommandClarification.fromJson(Map<String, dynamic> json) {
    return CommandClarification(
      code: _stringValue(json['code']) ?? 'command_clarification_required',
      question: CommandLocalizedText.fromJson(json['question']),
      options: _mapList(
        json['options'],
      ).map(CommandClarificationOption.fromJson).toList(growable: false),
    );
  }
}

class CommandPlan {
  const CommandPlan({
    required this.schemaVersion,
    required this.disposition,
    required this.commandType,
    required this.originalInstruction,
    required this.planHash,
    required this.parserSource,
    required this.confirmationPolicy,
    required this.summary,
    this.sessionId,
    this.selectedEditId,
    this.targetEditId,
    this.sourceEditId,
    this.revertEditId,
    this.normalizedSlots = const <String, dynamic>{},
    this.action = const <String, dynamic>{},
    this.clarification,
  });

  final String schemaVersion;
  final String disposition;
  final String commandType;
  final String originalInstruction;
  final String? sessionId;
  final String? selectedEditId;
  final String? targetEditId;
  final String? sourceEditId;
  final String? revertEditId;
  final Map<String, dynamic> normalizedSlots;
  final Map<String, dynamic> action;
  final String confirmationPolicy;
  final String planHash;
  final String parserSource;
  final CommandLocalizedText summary;
  final CommandClarification? clarification;

  bool get isReady => disposition == 'ready';
  bool get requiresClarification => disposition == 'clarification_required';
  bool get isPhotoGit =>
      commandType == 'photo_git_merge' || commandType == 'photo_git_revert';

  factory CommandPlan.fromJson(Map<String, dynamic> json) {
    final clarification = _mapValue(json['clarification']);
    return CommandPlan(
      schemaVersion: _stringValue(json['schema_version']) ?? '',
      disposition: _stringValue(json['disposition']) ?? 'unsupported',
      commandType: _stringValue(json['command_type']) ?? 'unknown',
      originalInstruction: _stringValue(json['original_instruction']) ?? '',
      sessionId: _stringValue(json['session_id']),
      selectedEditId: _stringValue(json['selected_edit_id']),
      targetEditId: _stringValue(json['target_edit_id']),
      sourceEditId: _stringValue(json['source_edit_id']),
      revertEditId: _stringValue(json['revert_edit_id']),
      normalizedSlots: _mapValue(json['normalized_slots']),
      action: _mapValue(json['action']),
      confirmationPolicy: _stringValue(json['confirmation_policy']) ?? 'none',
      planHash: _stringValue(json['plan_hash']) ?? '',
      parserSource: _stringValue(json['parser_source']) ?? '',
      summary: CommandLocalizedText.fromJson(json['summary']),
      clarification: clarification.isEmpty
          ? null
          : CommandClarification.fromJson(clarification),
    );
  }
}

enum PhotoGitOperation {
  merge('merge'),
  selectiveRevert('selective_revert');

  const PhotoGitOperation(this.wireValue);

  final String wireValue;

  static PhotoGitOperation fromWire(String? value) {
    return value == 'selective_revert'
        ? PhotoGitOperation.selectiveRevert
        : PhotoGitOperation.merge;
  }
}

class PhotoGitSelector {
  const PhotoGitSelector({
    this.region,
    this.maskType,
    this.parameters = const <String>[],
    this.allContributions = false,
  });

  final String? region;
  final String? maskType;
  final List<String> parameters;
  final bool allContributions;

  Map<String, dynamic> toJson() => <String, dynamic>{
    if (region != null) 'region': region,
    if (maskType != null) 'mask_type': maskType,
    if (parameters.isNotEmpty) 'parameters': parameters,
    if (allContributions) 'all_contributions': true,
  };

  factory PhotoGitSelector.fromJson(Map<String, dynamic> json) {
    return PhotoGitSelector(
      region: _stringValue(json['region']),
      maskType: _stringValue(json['mask_type']),
      parameters: _stringList(json['parameters']),
      allContributions: _nullableBoolValue(json['all_contributions']) ?? false,
    );
  }
}

class PhotoGitRequest {
  const PhotoGitRequest({
    required this.operation,
    required this.targetEditId,
    this.sourceEditId,
    this.revertEditId,
    this.instruction = '',
    this.commandPlanHash,
    this.selectors = const <PhotoGitSelector>[],
    this.resolutions = const <String, String>{},
  });

  final PhotoGitOperation operation;
  final String targetEditId;
  final String? sourceEditId;
  final String? revertEditId;
  final String instruction;
  final String? commandPlanHash;
  final List<PhotoGitSelector> selectors;
  final Map<String, String> resolutions;

  Map<String, dynamic> toJson(String sessionId) => <String, dynamic>{
    'session_id': sessionId,
    'operation': operation.wireValue,
    'target_edit_id': targetEditId,
    if (sourceEditId != null) 'source_edit_id': sourceEditId,
    if (revertEditId != null) 'revert_edit_id': revertEditId,
    'instruction': instruction.trim(),
    if (commandPlanHash != null) 'command_plan_hash': commandPlanHash,
    'selectors': selectors.map((selector) => selector.toJson()).toList(),
    'resolutions': resolutions,
  };

  factory PhotoGitRequest.fromJson(Map<String, dynamic> json) {
    return PhotoGitRequest(
      operation: PhotoGitOperation.fromWire(_stringValue(json['operation'])),
      targetEditId: _stringValue(json['target_edit_id']) ?? '',
      sourceEditId: _stringValue(json['source_edit_id']),
      revertEditId: _stringValue(json['revert_edit_id']),
      instruction: _stringValue(json['instruction']) ?? '',
      commandPlanHash: _stringValue(json['command_plan_hash']),
      selectors: _mapList(
        json['selectors'],
      ).map(PhotoGitSelector.fromJson).toList(growable: false),
      resolutions: <String, String>{
        for (final entry in _mapValue(json['resolutions']).entries)
          entry.key: entry.value.toString(),
      },
    );
  }
}

class PhotoGitConflict {
  const PhotoGitConflict({
    required this.conflictId,
    required this.type,
    required this.region,
    required this.parameter,
    required this.allowedChoices,
    this.maskType,
    this.ancestorValue,
    this.targetValue,
    this.sourceValue,
    this.laterEditIds = const <String>[],
    this.resolvedChoice,
  });

  final String conflictId;
  final String type;
  final String region;
  final String? maskType;
  final String parameter;
  final List<String> allowedChoices;
  final dynamic ancestorValue;
  final dynamic targetValue;
  final dynamic sourceValue;
  final List<String> laterEditIds;
  final String? resolvedChoice;

  bool get isResolved =>
      resolvedChoice != null && allowedChoices.contains(resolvedChoice);

  factory PhotoGitConflict.fromJson(Map<String, dynamic> json) {
    return PhotoGitConflict(
      conflictId: _stringValue(json['conflict_id']) ?? '',
      type: _stringValue(json['type']) ?? 'merge',
      region: _stringValue(json['region']) ?? 'all',
      maskType: _stringValue(json['mask_type']),
      parameter: _stringValue(json['parameter']) ?? '',
      allowedChoices: _stringList(json['allowed_choices']),
      ancestorValue: json['ancestor_value'],
      targetValue: json['target_value'],
      sourceValue: json['source_value'],
      laterEditIds: _stringList(json['later_edit_ids']),
      resolvedChoice: _stringValue(json['resolved_choice']),
    );
  }
}

class PhotoGitPlan {
  const PhotoGitPlan({
    required this.status,
    required this.operation,
    required this.targetEditId,
    required this.planHash,
    required this.message,
    this.sourceEditIds = const <String>[],
    this.revertEditId,
    this.commonAncestorEditId,
    this.targetResultUrl,
    this.appliedContributions = const <Map<String, dynamic>>[],
    this.removedContributions = const <Map<String, dynamic>>[],
    this.conflicts = const <PhotoGitConflict>[],
  });

  final String status;
  final PhotoGitOperation operation;
  final String targetEditId;
  final List<String> sourceEditIds;
  final String? revertEditId;
  final String? commonAncestorEditId;
  final String planHash;
  final String message;
  final String? targetResultUrl;
  final List<Map<String, dynamic>> appliedContributions;
  final List<Map<String, dynamic>> removedContributions;
  final List<PhotoGitConflict> conflicts;

  bool get isReady => status == 'ready';
  bool get hasUnresolvedConflicts =>
      conflicts.any((conflict) => !conflict.isResolved);

  factory PhotoGitPlan.fromJson(
    Map<String, dynamic> json, {
    required ImageUrlBuilder buildImageUrl,
  }) {
    final targetResultPath = _stringValue(json['target_result_url']);
    return PhotoGitPlan(
      status: _stringValue(json['status']) ?? 'unsupported',
      operation: PhotoGitOperation.fromWire(_stringValue(json['operation'])),
      targetEditId: _stringValue(json['target_edit_id']) ?? '',
      sourceEditIds: _stringList(json['source_edit_ids']),
      revertEditId: _stringValue(json['revert_edit_id']),
      commonAncestorEditId: _stringValue(json['common_ancestor_edit_id']),
      planHash: _stringValue(json['plan_hash']) ?? '',
      message: _stringValue(json['message']) ?? '',
      targetResultUrl: targetResultPath == null
          ? null
          : buildImageUrl(targetResultPath),
      appliedContributions: _mapList(json['applied_contributions']),
      removedContributions: _mapList(json['removed_contributions']),
      conflicts: _mapList(
        json['conflicts'],
      ).map(PhotoGitConflict.fromJson).toList(growable: false),
    );
  }
}

class PhotoGitPreview {
  const PhotoGitPreview({
    required this.planHash,
    required this.resultUrl,
    required this.targetEditId,
    this.targetResultUrl,
  });

  final String planHash;
  final String resultUrl;
  final String targetEditId;
  final String? targetResultUrl;

  factory PhotoGitPreview.fromJson(
    Map<String, dynamic> json, {
    required ImageUrlBuilder buildImageUrl,
  }) {
    final resultPath =
        _stringValue(json['result_url']) ??
        _stringValue(json['result_saved_path']);
    if (resultPath == null) {
      throw const FormatException('Photo Git preview is missing a result path');
    }
    final targetPath = _stringValue(json['target_result_url']);
    return PhotoGitPreview(
      planHash: _stringValue(json['plan_hash']) ?? '',
      resultUrl: buildImageUrl(resultPath),
      targetEditId: _stringValue(json['target_edit_id']) ?? '',
      targetResultUrl: targetPath == null ? null : buildImageUrl(targetPath),
    );
  }
}

class PhotoGitMetadata {
  const PhotoGitMetadata({
    required this.operation,
    required this.targetEditId,
    required this.sourceEditIds,
    required this.appliedContributions,
    required this.removedContributions,
    required this.conflicts,
    required this.resolutions,
    this.revertedEditId,
    this.commonAncestorEditId,
    this.schemaVersion,
    this.rendererVersion,
    this.planHash,
  });

  final PhotoGitOperation operation;
  final String targetEditId;
  final List<String> sourceEditIds;
  final String? revertedEditId;
  final String? commonAncestorEditId;
  final String? schemaVersion;
  final String? rendererVersion;
  final String? planHash;
  final List<Map<String, dynamic>> appliedContributions;
  final List<Map<String, dynamic>> removedContributions;
  final List<Map<String, dynamic>> conflicts;
  final Map<String, dynamic> resolutions;

  factory PhotoGitMetadata.fromJson(Map<String, dynamic> json) {
    return PhotoGitMetadata(
      operation: PhotoGitOperation.fromWire(_stringValue(json['operation'])),
      targetEditId: _stringValue(json['target_edit_id']) ?? '',
      sourceEditIds: _stringList(json['source_edit_ids']),
      revertedEditId: _stringValue(json['reverted_edit_id']),
      commonAncestorEditId: _stringValue(json['common_ancestor_edit_id']),
      schemaVersion: _stringValue(json['schema_version']),
      rendererVersion: _stringValue(json['renderer_version']),
      planHash: _stringValue(json['plan_hash']),
      appliedContributions: _mapList(json['applied_contributions']),
      removedContributions: _mapList(json['removed_contributions']),
      conflicts: _mapList(json['conflicts']),
      resolutions: _mapValue(json['resolutions']),
    );
  }
}

List<Map<String, dynamic>> _mapList(dynamic value) {
  if (value is! List) {
    return const <Map<String, dynamic>>[];
  }
  return value
      .whereType<Map>()
      .map((item) => Map<String, dynamic>.from(item))
      .toList(growable: false);
}

class EditContractUnitSchema {
  const EditContractUnitSchema({
    required this.unitId,
    required this.displayPrecision,
    required this.labels,
  });

  final String unitId;
  final int displayPrecision;
  final Map<String, String> labels;

  factory EditContractUnitSchema.fromJson(Map<String, dynamic> json) {
    return EditContractUnitSchema(
      unitId: _stringValue(json['unit_id']) ?? '',
      displayPrecision: _nullableIntValue(json['display_precision']) ?? 2,
      labels: _localizedStringMap(json['labels']),
    );
  }
}

class EditContractMetricSchema {
  const EditContractMetricSchema({
    required this.metricId,
    required this.metricVersion,
    required this.unit,
    required this.labels,
    required this.descriptions,
  });

  final String metricId;
  final String metricVersion;
  final String unit;
  final Map<String, String> labels;
  final Map<String, String> descriptions;

  factory EditContractMetricSchema.fromJson(Map<String, dynamic> json) {
    return EditContractMetricSchema(
      metricId: _stringValue(json['metric_id']) ?? '',
      metricVersion: _stringValue(json['metric_version']) ?? '',
      unit: _stringValue(json['unit']) ?? '',
      labels: _localizedStringMap(json['labels']),
      descriptions: _localizedStringMap(json['descriptions']),
    );
  }
}

/// Public display metadata exposed by `/edit/contracts/schema`.
///
/// The UI indexes definitions by identifier instead of switching on metric
/// names, so adding a backend metric does not require parser or panel changes.
class EditContractSchema {
  const EditContractSchema({
    required this.metricRegistryVersion,
    required this.metrics,
    required this.units,
  });

  final String metricRegistryVersion;
  final List<EditContractMetricSchema> metrics;
  final List<EditContractUnitSchema> units;

  EditContractMetricSchema? metricFor(String metricId) {
    for (final metric in metrics) {
      if (metric.metricId == metricId) {
        return metric;
      }
    }
    return null;
  }

  EditContractUnitSchema? unitFor(String unitId) {
    for (final unit in units) {
      if (unit.unitId == unitId) {
        return unit;
      }
    }
    return null;
  }

  factory EditContractSchema.fromJson(Map<String, dynamic> json) {
    return EditContractSchema(
      metricRegistryVersion:
          _stringValue(json['metric_registry_version']) ?? '',
      metrics: _mapList(json['metrics'])
          .map(EditContractMetricSchema.fromJson)
          .where((metric) => metric.metricId.isNotEmpty)
          .toList(growable: false),
      units: _mapList(json['units'])
          .map(EditContractUnitSchema.fromJson)
          .where((unit) => unit.unitId.isNotEmpty)
          .toList(growable: false),
    );
  }
}

/// A hard, measurable condition attached to an edit contract.
///
/// Metric identifiers intentionally remain strings. The backend registry can
/// add metrics without requiring a Flutter release, and unknown metrics still
/// remain inspectable instead of failing response parsing.
class EditContractConstraint {
  const EditContractConstraint({
    required this.constraintId,
    required this.metricId,
    required this.metricVersion,
    required this.subjectRegion,
    required this.maskType,
    required this.operator,
    required this.threshold,
    required this.unit,
    required this.thresholdSource,
    required this.referenceMode,
    required this.source,
    required this.hard,
    required this.capabilityRequirements,
    this.profileId,
    this.sourceText,
    this.sourceStart,
    this.sourceEnd,
    this.language,
    this.confidence,
    this.sourceEvidence,
    this.evidence = const <Map<String, dynamic>>[],
  });

  final String constraintId;
  final String metricId;
  final String metricVersion;
  final String subjectRegion;
  final String maskType;
  final String operator;
  final double? threshold;
  final String unit;
  final String thresholdSource;
  final String referenceMode;
  final String source;
  final bool hard;
  final List<String> capabilityRequirements;
  final String? profileId;
  final String? sourceText;
  final int? sourceStart;
  final int? sourceEnd;
  final String? language;
  final double? confidence;
  final Map<String, dynamic>? sourceEvidence;
  final List<Map<String, dynamic>> evidence;

  factory EditContractConstraint.fromJson(Map<String, dynamic> json) {
    return EditContractConstraint(
      constraintId: _stringValue(json['constraint_id']) ?? '',
      metricId: _stringValue(json['metric_id']) ?? 'unknown_metric',
      metricVersion: _stringValue(json['metric_version']) ?? '',
      subjectRegion: _stringValue(json['subject_region']) ?? 'all',
      maskType: _stringValue(json['mask_type']) ?? 'none',
      operator: _stringValue(json['operator']) ?? '<=',
      threshold: _nullableDoubleValue(json['threshold']),
      unit: _stringValue(json['unit']) ?? '',
      thresholdSource:
          _stringValue(json['threshold_source']) ?? 'policy_default',
      referenceMode: _stringValue(json['reference_mode']) ?? 'absolute_outcome',
      source: _stringValue(json['source']) ?? 'policy',
      hard: _nullableBoolValue(json['hard']) ?? true,
      capabilityRequirements: _stringList(json['capability_requirements']),
      profileId: _stringValue(json['profile_id']),
      sourceText: _stringValue(json['source_text']),
      sourceStart: _nullableIntValue(json['source_start']),
      sourceEnd: _nullableIntValue(json['source_end']),
      language: _stringValue(json['language']),
      confidence: _nullableDoubleValue(json['confidence']),
      sourceEvidence: json['source_evidence'] is Map
          ? Map<String, dynamic>.from(json['source_evidence'] as Map)
          : null,
      evidence: _mapList(json['evidence']),
    );
  }
}

class EditContractCheck {
  const EditContractCheck({
    required this.constraintId,
    required this.metricId,
    required this.metricVersion,
    required this.operator,
    required this.unit,
    required this.policyThreshold,
    required this.effectiveThreshold,
    required this.baselineValue,
    required this.candidateValue,
    required this.thresholdSource,
    required this.passed,
    required this.details,
  });

  final String constraintId;
  final String metricId;
  final String metricVersion;
  final String operator;
  final String unit;
  final double? policyThreshold;
  final double? effectiveThreshold;
  final double? baselineValue;
  final double? candidateValue;
  final String thresholdSource;
  final bool passed;
  final Map<String, dynamic> details;

  factory EditContractCheck.fromJson(Map<String, dynamic> json) {
    return EditContractCheck(
      constraintId: _stringValue(json['constraint_id']) ?? '',
      metricId: _stringValue(json['metric_id']) ?? 'unknown_metric',
      metricVersion: _stringValue(json['metric_version']) ?? '',
      operator: _stringValue(json['operator']) ?? '<=',
      unit: _stringValue(json['unit']) ?? '',
      policyThreshold: _nullableDoubleValue(json['policy_threshold']),
      effectiveThreshold: _nullableDoubleValue(json['effective_threshold']),
      baselineValue: _nullableDoubleValue(json['baseline_value']),
      candidateValue: _nullableDoubleValue(json['candidate_value']),
      thresholdSource:
          _stringValue(json['threshold_source']) ?? 'policy_default',
      passed: _nullableBoolValue(json['passed']) ?? false,
      details: _mapValue(json['details']),
    );
  }
}

class EditContractAttempt {
  const EditContractAttempt({
    required this.scale,
    required this.checks,
    required this.passed,
    required this.renderMs,
    required this.verificationMs,
    this.failureReason,
  });

  final double? scale;
  final List<EditContractCheck> checks;
  final bool passed;
  final double? renderMs;
  final double? verificationMs;
  final String? failureReason;

  factory EditContractAttempt.fromJson(Map<String, dynamic> json) {
    return EditContractAttempt(
      scale: _nullableDoubleValue(json['scale']),
      checks: _mapList(
        json['checks'],
      ).map(EditContractCheck.fromJson).toList(growable: false),
      passed: _nullableBoolValue(json['passed']) ?? false,
      renderMs: _nullableDoubleValue(json['render_ms']),
      verificationMs: _nullableDoubleValue(json['verification_ms']),
      failureReason: _stringValue(json['failure_reason']),
    );
  }
}

class EditContractMetadata {
  const EditContractMetadata({
    required this.status,
    required this.contractHash,
    required this.schemaVersion,
    required this.semanticRegistryVersion,
    required this.metricRegistryVersion,
    required this.parserVersion,
    required this.searchPolicyVersion,
    required this.targetEditId,
    required this.requestedScale,
    required this.appliedScale,
    required this.constraints,
    required this.checks,
    required this.attempts,
    required this.timings,
    required this.requestedParameters,
    required this.actualParameters,
    required this.requestedEditPlan,
    this.selectedTargetBaselinePath,
    this.renderAnchorPath,
    this.maskSourcePath,
    this.clientRequestId,
    this.failureReason,
  });

  final String status;
  final String? contractHash;
  final String? schemaVersion;
  final String? semanticRegistryVersion;
  final String? metricRegistryVersion;
  final String? parserVersion;
  final String? searchPolicyVersion;
  final String? targetEditId;
  final double? requestedScale;
  final double? appliedScale;
  final List<EditContractConstraint> constraints;
  final List<EditContractCheck> checks;
  final List<EditContractAttempt> attempts;
  final Map<String, dynamic> timings;
  final Map<String, dynamic> requestedParameters;
  final Map<String, dynamic> actualParameters;
  final Map<String, dynamic> requestedEditPlan;
  final String? selectedTargetBaselinePath;
  final String? renderAnchorPath;
  final String? maskSourcePath;
  final String? clientRequestId;
  final String? failureReason;

  bool get isSuccessful => status == 'passed' || status == 'adjusted';

  bool get wasAdjusted =>
      status == 'adjusted' ||
      (appliedScale != null &&
          requestedScale != null &&
          appliedScale! < requestedScale! - 0.000001);

  int get passedCheckCount => checks.where((check) => check.passed).length;

  factory EditContractMetadata.fromJson(Map<String, dynamic> json) {
    final contractIr = _mapValue(json['contract_ir']);
    return EditContractMetadata(
      status: (_stringValue(json['status']) ?? 'unknown').toLowerCase(),
      contractHash: _stringValue(json['contract_hash']),
      schemaVersion: _stringValue(contractIr['schema_version']),
      semanticRegistryVersion: _stringValue(
        contractIr['semantic_registry_version'],
      ),
      metricRegistryVersion: _stringValue(
        contractIr['metric_registry_version'],
      ),
      parserVersion: _stringValue(contractIr['parser_version']),
      searchPolicyVersion: _stringValue(json['search_policy_version']),
      targetEditId: _stringValue(json['target_edit_id']),
      requestedScale: _nullableDoubleValue(json['requested_scale']),
      appliedScale: _nullableDoubleValue(json['applied_scale']),
      constraints: _mapList(
        contractIr['constraints'],
      ).map(EditContractConstraint.fromJson).toList(growable: false),
      checks: _mapList(
        json['checks'],
      ).map(EditContractCheck.fromJson).toList(growable: false),
      attempts: _mapList(
        json['attempts'],
      ).map(EditContractAttempt.fromJson).toList(growable: false),
      timings: _mapValue(json['timings']),
      requestedParameters: _mapValue(json['requested_parameter_vector']),
      actualParameters: _mapValue(json['actual_parameter_vector']),
      requestedEditPlan: _mapValue(json['requested_edit_plan']),
      selectedTargetBaselinePath: _stringValue(
        json['selected_target_baseline_path'],
      ),
      renderAnchorPath: _stringValue(json['render_anchor_path']),
      maskSourcePath: _stringValue(json['mask_source_path']),
      clientRequestId: _stringValue(json['client_request_id']),
      failureReason: _stringValue(json['failure_reason']),
    );
  }
}

Map<String, String> _localizedStringMap(dynamic value) {
  if (value is! Map) {
    return const <String, String>{};
  }
  return <String, String>{
    for (final entry in value.entries)
      if (entry.value != null) entry.key.toString(): entry.value.toString(),
  };
}

class AutoModelMetadata {
  const AutoModelMetadata({
    required this.schemaVersion,
    required this.comparisonId,
    required this.modelKey,
    required this.modelFamily,
    required this.sourceEditId,
    required this.sourceHistoryFingerprint,
    required this.assetIdentity,
    required this.runtimeMetadata,
    required this.timingsMs,
    required this.warningFlags,
  });

  final String schemaVersion;
  final String comparisonId;
  final String modelKey;
  final String modelFamily;
  final String sourceEditId;
  final String sourceHistoryFingerprint;
  final Map<String, dynamic> assetIdentity;
  final Map<String, dynamic> runtimeMetadata;
  final Map<String, dynamic> timingsMs;
  final List<String> warningFlags;

  factory AutoModelMetadata.fromJson(Map<String, dynamic> json) {
    return AutoModelMetadata(
      schemaVersion: _stringValue(json['schema_version']) ?? '',
      comparisonId: _stringValue(json['comparison_id']) ?? '',
      modelKey: _stringValue(json['model_key']) ?? '',
      modelFamily: _stringValue(json['model_family']) ?? '',
      sourceEditId: _stringValue(json['source_edit_id']) ?? 'original',
      sourceHistoryFingerprint:
          _stringValue(json['source_history_fingerprint']) ?? '',
      assetIdentity: _mapValue(json['asset_identity']),
      runtimeMetadata: _mapValue(json['runtime_metadata']),
      timingsMs: _mapValue(json['timings_ms']),
      warningFlags: _stringList(json['warning_flags']),
    );
  }
}

class AutoModelCandidateError {
  const AutoModelCandidateError({
    required this.code,
    required this.message,
    required this.retryable,
    required this.details,
  });

  final String code;
  final String message;
  final bool retryable;
  final Map<String, dynamic> details;

  factory AutoModelCandidateError.fromJson(Map<String, dynamic> json) {
    return AutoModelCandidateError(
      code: _stringValue(json['code']) ?? 'auto_model_failed',
      message: _stringValue(json['message']) ?? 'Automatic enhancement failed',
      retryable: _nullableBoolValue(json['retryable']) ?? false,
      details: _mapValue(json['details']),
    );
  }
}

class AutoModelCandidate {
  const AutoModelCandidate({
    required this.status,
    required this.modelKey,
    required this.editId,
    required this.parentEditId,
    required this.resultUrl,
    required this.metadata,
    required this.error,
    required this.idempotentReplay,
  });

  final String status;
  final String modelKey;
  final String? editId;
  final String? parentEditId;
  final String? resultUrl;
  final AutoModelMetadata? metadata;
  final AutoModelCandidateError? error;
  final bool idempotentReplay;

  bool get isSuccess =>
      status == 'success' && editId != null && resultUrl != null;
  bool get isError => status == 'error';

  factory AutoModelCandidate.fromJson(
    Map<String, dynamic> json, {
    required ImageUrlBuilder buildImageUrl,
    String? fallbackModelKey,
  }) {
    final resultPath =
        _stringValue(json['result_url']) ??
        _stringValue(json['result_saved_path']);
    final rawMetadata = json['auto_model'];
    final rawError = json['error'];
    return AutoModelCandidate(
      status: _stringValue(json['status']) ?? 'error',
      modelKey:
          _stringValue(json['model_key']) ?? fallbackModelKey ?? 'unknown',
      editId: _stringValue(json['edit_id']),
      parentEditId: _stringValue(json['parent_edit_id']),
      resultUrl: resultPath == null ? null : buildImageUrl(resultPath),
      metadata: rawMetadata is Map
          ? AutoModelMetadata.fromJson(Map<String, dynamic>.from(rawMetadata))
          : null,
      error: rawError is Map
          ? AutoModelCandidateError.fromJson(
              Map<String, dynamic>.from(rawError),
            )
          : null,
      idempotentReplay: _nullableBoolValue(json['idempotent_replay']) ?? false,
    );
  }
}

class AutoModelComparison {
  const AutoModelComparison({
    required this.schemaVersion,
    required this.comparisonId,
    required this.status,
    required this.sessionId,
    required this.sourceEditId,
    required this.parentEditId,
    required this.sourceHistoryFingerprint,
    required this.sourceUrl,
    required this.candidates,
    required this.executionMode,
    required this.idempotentReplay,
    required this.batchTimingsMs,
  });

  final String schemaVersion;
  final String comparisonId;
  final String status;
  final String sessionId;
  final String sourceEditId;
  final String? parentEditId;
  final String sourceHistoryFingerprint;
  final String? sourceUrl;
  final Map<String, AutoModelCandidate> candidates;
  final String executionMode;
  final bool idempotentReplay;
  final Map<String, dynamic> batchTimingsMs;

  bool get isSuccess => status == 'success';
  bool get isPartialSuccess => status == 'partial_success';
  bool get isError => status == 'error';
  Iterable<AutoModelCandidate> get successfulCandidates =>
      candidates.values.where((candidate) => candidate.isSuccess);

  factory AutoModelComparison.fromJson(
    Map<String, dynamic> json, {
    required ImageUrlBuilder buildImageUrl,
  }) {
    final rawCandidates = _mapValue(json['candidates']);
    final source = _mapValue(json['source']);
    final sourcePath =
        _stringValue(source['url']) ?? _stringValue(source['saved_path']);
    return AutoModelComparison(
      schemaVersion: _stringValue(json['schema_version']) ?? '',
      comparisonId: _stringValue(json['comparison_id']) ?? '',
      status: _stringValue(json['status']) ?? 'error',
      sessionId: _stringValue(json['session_id']) ?? '',
      sourceEditId: _stringValue(json['source_edit_id']) ?? 'original',
      parentEditId: _stringValue(json['parent_edit_id']),
      sourceHistoryFingerprint:
          _stringValue(json['source_history_fingerprint']) ?? '',
      sourceUrl: sourcePath == null ? null : buildImageUrl(sourcePath),
      candidates: <String, AutoModelCandidate>{
        for (final entry in rawCandidates.entries)
          if (entry.value is Map)
            entry.key: AutoModelCandidate.fromJson(
              Map<String, dynamic>.from(entry.value as Map),
              buildImageUrl: buildImageUrl,
              fallbackModelKey: entry.key,
            ),
      },
      executionMode: _stringValue(json['execution_mode']) ?? 'sequential',
      idempotentReplay: _nullableBoolValue(json['idempotent_replay']) ?? false,
      batchTimingsMs: _mapValue(json['batch_timings_ms']),
    );
  }
}

class EditHistoryItem {
  const EditHistoryItem({
    required this.sessionId,
    required this.editId,
    required this.parentEditId,
    required this.prompt,
    required this.resultUrl,
    required this.originalUrl,
    required this.resolvedIntent,
    required this.editMode,
    required this.parserSource,
    required this.explanation,
    required this.engine,
    required this.parameters,
    required this.parameterOverrides,
    required this.maskInfo,
    required this.createdAt,
    required this.presetName,
    this.style,
    this.adaptive = const <String, dynamic>{},
    this.photoGit,
    this.editContract,
    this.autoModel,
    this.visualAnchor = const <String, dynamic>{},
  });

  final String sessionId;
  final String editId;
  final String? parentEditId;
  final String prompt;
  final String resultUrl;
  final String? originalUrl;
  final String? resolvedIntent;
  final String editMode;
  final String? parserSource;
  final String? explanation;
  final String engine;
  final Map<String, dynamic> parameters;
  final Map<String, dynamic> parameterOverrides;
  final Map<String, dynamic> maskInfo;
  final DateTime? createdAt;
  final String? presetName;
  final StyleMetadata? style;
  final Map<String, dynamic> adaptive;
  final PhotoGitMetadata? photoGit;
  final EditContractMetadata? editContract;
  final AutoModelMetadata? autoModel;
  final Map<String, dynamic> visualAnchor;

  factory EditHistoryItem.fromJson(
    Map<String, dynamic> json, {
    required ImageUrlBuilder buildImageUrl,
  }) {
    final resultPath =
        _stringValue(json['result_url']) ??
        _stringValue(json['result_saved_path']) ??
        _stringValue(json['result_image_path']);
    if (resultPath == null) {
      throw const FormatException('Edit response is missing a result path');
    }
    final originalPath =
        _stringValue(json['original_url']) ??
        _stringValue(json['original_saved_path']) ??
        _stringValue(json['original_image_path']);
    final createdAtText = _stringValue(json['created_at']);
    return EditHistoryItem(
      sessionId: _stringValue(json['session_id']) ?? '',
      editId: _stringValue(json['edit_id']) ?? '',
      parentEditId: _stringValue(json['parent_edit_id']),
      prompt:
          _stringValue(json['prompt']) ??
          _stringValue(json['user_prompt']) ??
          '',
      resultUrl: buildImageUrl(resultPath),
      originalUrl: originalPath == null ? null : buildImageUrl(originalPath),
      resolvedIntent: _stringValue(json['resolved_intent']),
      editMode: _stringValue(json['edit_mode']) ?? 'prompt',
      parserSource: _stringValue(json['parser_source']),
      explanation: _stringValue(json['explanation']),
      engine: _stringValue(json['engine']) ?? 'opencv',
      parameters: _mapValue(json['engine_parameters'] ?? json['parameters']),
      parameterOverrides: _mapValue(json['parameter_overrides']),
      maskInfo: _mapValue(json['mask_info']),
      createdAt: createdAtText == null
          ? null
          : DateTime.tryParse(createdAtText),
      presetName: _stringValue(json['preset_name']),
      style: json['style'] is Map
          ? StyleMetadata.fromJson(
              Map<String, dynamic>.from(json['style'] as Map),
            )
          : null,
      adaptive: _mapValue(json['adaptive']),
      photoGit: json['photo_git'] is Map
          ? PhotoGitMetadata.fromJson(
              Map<String, dynamic>.from(json['photo_git'] as Map),
            )
          : null,
      editContract: json['edit_contract'] is Map
          ? EditContractMetadata.fromJson(
              Map<String, dynamic>.from(json['edit_contract'] as Map),
            )
          : null,
      autoModel: json['auto_model'] is Map
          ? AutoModelMetadata.fromJson(
              Map<String, dynamic>.from(json['auto_model'] as Map),
            )
          : null,
      visualAnchor: _mapValue(json['visual_anchor']),
    );
  }

  dynamic _adaptiveValue(String key) {
    final topLevelValue = adaptive[key];
    if (topLevelValue != null) {
      return topLevelValue;
    }
    return _mapValue(adaptive['state'])[key];
  }

  bool get hasAdaptiveInfo => adaptiveOperations.isNotEmpty;

  String? get adaptiveSchemaVersion =>
      _stringValue(_adaptiveValue('schema_version'));

  bool? get adaptiveApplied => _nullableBoolValue(_adaptiveValue('applied'));

  String? get adaptivePolicyVersion =>
      _stringValue(_adaptiveValue('policy_version')) ??
      _stringValue(_adaptiveValue('policy'));

  String? get adaptiveReason => _stringValue(_adaptiveValue('reason'));

  String? get adaptiveAxis =>
      _stringValue(_adaptiveValue('axis')) ??
      _stringValue(_adaptiveValue('primary_parameter'));

  double? get adaptiveDeltaFromParent =>
      _nullableDoubleValue(_adaptiveValue('delta_from_parent'));

  double? get adaptiveCurrentValue =>
      _nullableDoubleValue(_adaptiveValue('current_value'));

  double? get adaptiveNextValue =>
      _nullableDoubleValue(_adaptiveValue('next_value'));

  double? get adaptiveLowerBound =>
      _nullableDoubleValue(_adaptiveValue('lower_bound'));

  double? get adaptiveUpperBound =>
      _nullableDoubleValue(_adaptiveValue('upper_bound'));

  double? get adaptiveStepBefore =>
      _nullableDoubleValue(_adaptiveValue('step_before'));

  double? get adaptiveStepAfter =>
      _nullableDoubleValue(_adaptiveValue('step_after'));

  bool? get adaptiveConverged =>
      _nullableBoolValue(_adaptiveValue('converged'));

  Map<String, dynamic> get adaptivePolicyRegistry =>
      _mapValue(_adaptiveValue('policy_registry'));

  ParameterMetadataCatalog parameterMetadataCatalog({
    ManualSchema? manualSchema,
  }) {
    return ParameterMetadataCatalog.fromSources(
      manualSchema: manualSchema,
      policyRegistry: adaptivePolicyRegistry,
    );
  }

  List<AdaptiveOperation> get adaptiveOperations =>
      adaptiveOperationsFor(parameterMetadataCatalog());

  List<AdaptiveOperation> adaptiveOperationsFor(
    ParameterMetadataCatalog metadataCatalog,
  ) {
    final schema = adaptiveSchemaVersion;
    if (schema != null &&
        schema != 'adaptive_prompt_v1' &&
        schema != 'adaptive_prompt_v2') {
      return const <AdaptiveOperation>[];
    }
    final defaultRegion = _stringValue(_adaptiveValue('region')) ?? region;
    final rawOperations = adaptive['operations'];
    if (rawOperations is List) {
      final parsed = rawOperations
          .map(
            (value) => AdaptiveOperation.tryParse(
              value,
              defaultRegion: defaultRegion,
              metadataCatalog: metadataCatalog,
            ),
          )
          .whereType<AdaptiveOperation>()
          .toList(growable: false);
      if (parsed.isNotEmpty) {
        return parsed;
      }
    }

    final axis = adaptiveAxis;
    if (axis == null || !metadataCatalog.contains(axis)) {
      return const <AdaptiveOperation>[];
    }
    final hasMeaningfulState =
        adaptiveReason != null ||
        _stringValue(_adaptiveValue('relation')) != null ||
        adaptiveCurrentValue != null ||
        adaptiveNextValue != null ||
        adaptiveDeltaFromParent != null ||
        adaptiveLowerBound != null ||
        adaptiveUpperBound != null ||
        adaptiveStepBefore != null ||
        adaptiveStepAfter != null ||
        _nullableIntValue(_adaptiveValue('direction')) != null ||
        adaptiveApplied != null ||
        adaptiveConverged != null;
    if (!hasMeaningfulState) {
      return const <AdaptiveOperation>[];
    }
    return <AdaptiveOperation>[
      AdaptiveOperation(
        axis: axis,
        region: defaultRegion,
        relation: _stringValue(_adaptiveValue('relation')),
        reason: adaptiveReason,
        currentValue: adaptiveCurrentValue,
        nextValue: adaptiveNextValue,
        deltaFromParent: adaptiveDeltaFromParent,
        lowerBound: adaptiveLowerBound,
        upperBound: adaptiveUpperBound,
        stepBefore: adaptiveStepBefore,
        stepAfter: adaptiveStepAfter,
        direction: _nullableIntValue(_adaptiveValue('direction')),
        applied: adaptiveApplied,
        converged: adaptiveConverged,
      ),
    ];
  }

  String get region {
    final value =
        _stringValue(parameters['region']) ??
        _stringValue(maskInfo['target']) ??
        'all';
    return value;
  }

  String get targetLabel => regionLabel(region);

  String get modeLabel {
    if (resolvedIntent == 'apply_style') {
      return '風格';
    }
    switch (editMode) {
      case 'photo_git_merge':
        return '版本合併';
      case 'photo_git_revert':
        return '選擇性撤銷';
      case 'manual':
        return '手動調整';
      case 'reference':
        return '參考圖';
      case 'auto_model':
        return '自動修圖';
      default:
        return '指令';
    }
  }

  String get displayTitle {
    if (editMode == 'photo_git_merge') {
      return prompt.isEmpty ? '版本合併' : prompt;
    }
    if (editMode == 'photo_git_revert') {
      return prompt.isEmpty ? '選擇性撤銷' : prompt;
    }
    if (resolvedIntent == 'apply_style' && style != null) {
      return '${style!.displayName} · ${(style!.strength * 100).round()}%';
    }
    if (editMode == 'manual') {
      return '手動調整';
    }
    if (editMode == 'reference') {
      return '參考圖修圖';
    }
    if (editMode == 'auto_model') {
      return autoModel?.modelKey ?? '自動修圖';
    }
    return prompt.isEmpty ? (resolvedIntent ?? '指令修圖') : prompt;
  }

  bool get isDirectStyleEdit =>
      resolvedIntent == 'apply_style' && style != null;

  bool get isPhotoGit =>
      editMode == 'photo_git_merge' || editMode == 'photo_git_revert';

  Map<String, dynamic> parametersForDisplay(
    ParameterMetadataCatalog metadataCatalog,
  ) {
    if (!isDirectStyleEdit) {
      return parameters;
    }
    final strength = style!.strength.clamp(0.0, 1.0).toDouble();
    return <String, dynamic>{
      for (final entry in parameters.entries)
        entry.key: switch (entry.value) {
          final num value when metadataCatalog.metadataFor(entry.key) != null =>
            _interpolateStyleDisplayValue(
              value.toDouble(),
              metadataCatalog.metadataFor(entry.key)!.neutral,
              strength,
            ),
          _ => entry.value,
        },
    };
  }
}

class EditSession {
  const EditSession({required this.sessionId, required this.edits});

  final String sessionId;
  final List<EditHistoryItem> edits;

  factory EditSession.fromJson(
    Map<String, dynamic> json, {
    required ImageUrlBuilder buildImageUrl,
  }) {
    final rawEdits = json['edits'];
    final edits = rawEdits is List
        ? rawEdits
              .whereType<Map>()
              .map(
                (item) => EditHistoryItem.fromJson(
                  Map<String, dynamic>.from(item),
                  buildImageUrl: buildImageUrl,
                ),
              )
              .toList()
        : <EditHistoryItem>[];
    return EditSession(
      sessionId: _stringValue(json['session_id']) ?? '',
      edits: edits,
    );
  }
}

class ManualEditResponse {
  const ManualEditResponse({
    required this.sessionId,
    required this.sourceEditId,
    required this.editId,
    required this.editMode,
    required this.resultUrl,
    required this.originalUrl,
    required this.clientRequestId,
    required this.parameters,
    required this.parameterOverrides,
    required this.maskInfo,
    required this.explanation,
    this.style,
  });

  final String sessionId;
  final String sourceEditId;
  final String? editId;
  final String editMode;
  final String resultUrl;
  final String? originalUrl;
  final String? clientRequestId;
  final Map<String, dynamic> parameters;
  final Map<String, dynamic> parameterOverrides;
  final Map<String, dynamic> maskInfo;
  final String? explanation;
  final StyleMetadata? style;

  factory ManualEditResponse.fromJson(
    Map<String, dynamic> json, {
    required ImageUrlBuilder buildImageUrl,
  }) {
    final resultPath =
        _stringValue(json['result_url']) ??
        _stringValue(json['result_saved_path']);
    if (resultPath == null) {
      throw const FormatException('Manual response is missing a result path');
    }
    final originalPath = _stringValue(json['original_saved_path']);
    return ManualEditResponse(
      sessionId: _stringValue(json['session_id']) ?? '',
      sourceEditId:
          _stringValue(json['source_edit_id']) ??
          _stringValue(json['manual_source_edit_id']) ??
          '',
      editId: _stringValue(json['edit_id']),
      editMode: _stringValue(json['edit_mode']) ?? 'manual_preview',
      resultUrl: buildImageUrl(resultPath),
      originalUrl: originalPath == null ? null : buildImageUrl(originalPath),
      clientRequestId: _stringValue(json['client_request_id']),
      parameters: _mapValue(json['engine_parameters'] ?? json['parameters']),
      parameterOverrides: _mapValue(json['parameter_overrides']),
      maskInfo: _mapValue(json['mask_info']),
      explanation: _stringValue(json['explanation']),
      style: json['style'] is Map
          ? StyleMetadata.fromJson(
              Map<String, dynamic>.from(json['style'] as Map),
            )
          : null,
    );
  }

  EditHistoryItem toHistoryItem() {
    final id = editId;
    if (id == null) {
      throw StateError('A preview cannot be converted to history');
    }
    return EditHistoryItem(
      sessionId: sessionId,
      editId: id,
      parentEditId: sourceEditId,
      prompt: '',
      resultUrl: resultUrl,
      originalUrl: originalUrl,
      resolvedIntent: 'manual_adjustment',
      editMode: 'manual',
      parserSource: 'manual_parameters',
      explanation: explanation,
      engine: 'opencv',
      parameters: parameters,
      parameterOverrides: parameterOverrides,
      maskInfo: maskInfo,
      createdAt: DateTime.now().toUtc(),
      presetName: null,
      style: style,
    );
  }
}

String regionLabel(String region) {
  switch (region) {
    case 'sky':
      return '天空';
    case 'person':
      return '人物';
    case 'background':
      return '背景';
    case 'highlights':
      return '亮部';
    case 'shadows':
      return '暗部';
    case 'center':
      return '中央';
    case 'edges':
      return '邊緣';
    default:
      return '全圖';
  }
}

String styleFamilyLabel(String family) {
  switch (family) {
    case 'natural_clean':
      return '自然清透';
    case 'portrait_skin':
      return '人像膚色';
    case 'landscape_travel':
      return '風景旅行';
    case 'cinematic':
      return '電影敘事';
    case 'film_retro':
      return '底片復古';
    case 'black_white':
      return '黑白';
    case 'night_neon':
      return '夜景霓虹';
    case 'pastel_creative':
      return '粉彩創意';
    default:
      return family;
  }
}

String parserSourceLabel(String? source) {
  switch (source) {
    case 'llm':
      return 'LLM 解析';
    case 'rule_based_fallback':
      return '規則解析';
    case 'reference_mode':
      return '參考圖模式';
    case 'manual_parameters':
      return '手動參數';
    case null:
      return '';
    default:
      return source;
  }
}

String compactParameterSummary(
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
    entries.add('${metadata.label} $formatted');
    if (entries.length == limit) {
      break;
    }
  }
  return entries.join(' · ');
}

double _interpolateStyleDisplayValue(
  double fullStyleValue,
  double neutral,
  double strength,
) {
  return neutral + (fullStyleValue - neutral) * strength;
}
