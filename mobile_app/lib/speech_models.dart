import 'package:flutter/foundation.dart';

enum SpeechLanguageMode {
  traditionalChinese,
  english,
  automatic;

  String? get apiHint => switch (this) {
    SpeechLanguageMode.traditionalChinese => 'zh',
    SpeechLanguageMode.english => 'en',
    SpeechLanguageMode.automatic => null,
  };
}

@immutable
class SpeechTranscription {
  const SpeechTranscription({
    required this.transcript,
    required this.language,
    required this.languageSource,
    required this.modelId,
    required this.device,
    required this.dtype,
    required this.audioDurationMs,
    required this.timings,
  });

  final String transcript;
  final String language;
  final String languageSource;
  final String modelId;
  final String device;
  final String dtype;
  final int audioDurationMs;
  final Map<String, int> timings;

  factory SpeechTranscription.fromJson(Map<String, dynamic> json) {
    final transcript = json['transcript']?.toString().trim() ?? '';
    if (transcript.isEmpty) {
      throw const FormatException(
        'Speech response did not include a transcript.',
      );
    }
    final rawTimings = json['timings'];
    final timings = <String, int>{};
    if (rawTimings is Map) {
      for (final entry in rawTimings.entries) {
        final value = entry.value;
        if (value is num) {
          timings[entry.key.toString()] = value.round();
        }
      }
    }
    return SpeechTranscription(
      transcript: transcript,
      language: json['language']?.toString() ?? 'auto',
      languageSource: json['language_source']?.toString() ?? 'auto',
      modelId: json['model_id']?.toString() ?? '',
      device: json['device']?.toString() ?? '',
      dtype: json['dtype']?.toString() ?? '',
      audioDurationMs: (json['audio_duration_ms'] as num?)?.round() ?? 0,
      timings: Map.unmodifiable(timings),
    );
  }
}

@immutable
class RecordedSpeechAudio {
  const RecordedSpeechAudio({
    required this.bytes,
    this.filename = 'speech.wav',
    this.contentType = 'audio/wav',
  });

  final Uint8List bytes;
  final String filename;
  final String contentType;
}
