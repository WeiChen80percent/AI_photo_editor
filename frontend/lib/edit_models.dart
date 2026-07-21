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

double _doubleValue(dynamic value, {double fallback = 0}) {
  if (value is num) {
    return value.toDouble();
  }
  return double.tryParse(value?.toString() ?? '') ?? fallback;
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
    );
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
    switch (editMode) {
      case 'manual':
        return '手動調整';
      case 'reference':
        return '參考圖';
      default:
        return '指令';
    }
  }

  String get displayTitle {
    if (editMode == 'manual') {
      return '手動調整';
    }
    if (editMode == 'reference') {
      return '參考圖修圖';
    }
    return prompt.isEmpty ? (resolvedIntent ?? '指令修圖') : prompt;
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
    );
  }
}

const Map<String, String> parameterLabels = {
  'exposure': '曝光',
  'brightness': '亮度',
  'contrast': '對比',
  'highlights': '高光',
  'shadows': '陰影',
  'saturation': '飽和度',
  'temperature': '色溫',
  'sharpen': '銳化',
  'clarity': '清晰度',
  'dehaze': '去霧',
  'vignette': '暗角',
};

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
}) {
  final entries = <String>[];
  for (final entry in parameters.entries) {
    final label = parameterLabels[entry.key];
    final value = entry.value;
    if (label == null || value is! num || !_isMeaningful(entry.key, value)) {
      continue;
    }
    final normalized = value.toDouble();
    final formatted = normalized == normalized.roundToDouble()
        ? normalized.toInt().toString()
        : normalized.toStringAsFixed(2);
    entries.add('$label ${normalized > 0 ? '+' : ''}$formatted');
    if (entries.length == limit) {
      break;
    }
  }
  return entries.join(' · ');
}

bool _isMeaningful(String key, num value) {
  final neutral = key == 'contrast' || key == 'saturation' ? 1.0 : 0.0;
  return (value.toDouble() - neutral).abs() > 0.0001;
}
