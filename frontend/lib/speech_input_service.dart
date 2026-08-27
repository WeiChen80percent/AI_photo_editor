import 'dart:async';
import 'dart:typed_data';

import 'package:record/record.dart';

import 'speech_models.dart';

const int maxSpeechRecordingSeconds = 15;

class SpeechRecordingException implements Exception {
  const SpeechRecordingException(this.code, this.message);

  final String code;
  final String message;

  @override
  String toString() => message;
}

abstract class SpeechInputService {
  Future<void> start();

  Future<RecordedSpeechAudio> stop();

  Future<void> cancel();

  Future<void> dispose();
}

class RecordSpeechInputService implements SpeechInputService {
  RecordSpeechInputService({AudioRecorder? recorder})
    : _recorder = recorder ?? AudioRecorder();

  static const int sampleRate = 16_000;
  static const int numChannels = 1;

  final AudioRecorder _recorder;
  final List<Uint8List> _chunks = <Uint8List>[];
  StreamSubscription<Uint8List>? _subscription;
  Completer<void>? _streamDone;
  Object? _streamError;
  bool _isRecording = false;
  bool _disposed = false;

  @override
  Future<void> start() async {
    if (_disposed) {
      throw const SpeechRecordingException(
        'speech_recorder_unavailable',
        'Recorder has already been disposed.',
      );
    }
    if (_isRecording) {
      throw const SpeechRecordingException(
        'speech_recorder_busy',
        'A recording is already in progress.',
      );
    }

    try {
      // Starting the stream is the permission request. record_web 1.3.0's
      // permissions-query preflight can report false even when Chrome grants
      // getUserMedia, while startStream preserves the real browser exception.
      if (!await _recorder.isEncoderSupported(AudioEncoder.pcm16bits)) {
        throw const SpeechRecordingException(
          'speech_recorder_unavailable',
          'PCM16 recording is not supported on this platform.',
        );
      }

      _chunks.clear();
      _streamError = null;
      _streamDone = Completer<void>();
      final stream = await _recorder.startStream(
        const RecordConfig(
          encoder: AudioEncoder.pcm16bits,
          sampleRate: sampleRate,
          numChannels: numChannels,
          autoGain: true,
          echoCancel: true,
          noiseSuppress: true,
          streamBufferSize: 2048,
        ),
      );
      _subscription = stream.listen(
        (chunk) => _chunks.add(Uint8List.fromList(chunk)),
        onError: (Object error, StackTrace stackTrace) {
          _streamError = error;
          if (!(_streamDone?.isCompleted ?? true)) {
            _streamDone!.complete();
          }
        },
        onDone: () {
          if (!(_streamDone?.isCompleted ?? true)) {
            _streamDone!.complete();
          }
        },
      );
      _isRecording = true;
    } on SpeechRecordingException {
      rethrow;
    } catch (error) {
      await _resetAfterFailure();
      throw _mapRecorderError(error);
    }
  }

  @override
  Future<RecordedSpeechAudio> stop() async {
    if (!_isRecording) {
      throw const SpeechRecordingException(
        'speech_recorder_not_recording',
        'No recording is in progress.',
      );
    }
    _isRecording = false;
    try {
      await _recorder.stop();
      await _streamDone?.future.timeout(const Duration(seconds: 2));
      final streamError = _streamError;
      if (streamError != null) {
        throw streamError;
      }
      final pcmLength = _chunks.fold<int>(
        0,
        (sum, chunk) => sum + chunk.length,
      );
      if (pcmLength == 0 || pcmLength.isOdd) {
        throw const SpeechRecordingException(
          'speech_no_audio',
          'The microphone did not return usable audio.',
        );
      }
      // A browser worklet can deliver its final buffered frame just after the
      // 15-second UI timer fires. Cap that boundary here so a normal automatic
      // stop cannot be rejected by the backend as slightly over-length.
      const maxPcmLength =
          sampleRate * numChannels * 2 * maxSpeechRecordingSeconds;
      final usablePcmLength = pcmLength > maxPcmLength
          ? maxPcmLength
          : pcmLength;
      final pcm = Uint8List(usablePcmLength);
      var offset = 0;
      for (final chunk in _chunks) {
        if (offset >= usablePcmLength) {
          break;
        }
        final remaining = usablePcmLength - offset;
        final copyLength = chunk.length > remaining ? remaining : chunk.length;
        pcm.setRange(offset, offset + copyLength, chunk);
        offset += copyLength;
      }
      return RecordedSpeechAudio(bytes: buildPcm16Wav(pcm));
    } on SpeechRecordingException {
      rethrow;
    } catch (error) {
      throw _mapRecorderError(error);
    } finally {
      await _clearStreamState();
    }
  }

  @override
  Future<void> cancel() async {
    if (_disposed) {
      return;
    }
    _isRecording = false;
    try {
      await _recorder.cancel();
    } catch (_) {
      // Cancellation is best-effort; state is cleared below either way.
    } finally {
      await _clearStreamState();
    }
  }

  @override
  Future<void> dispose() async {
    if (_disposed) {
      return;
    }
    await cancel();
    _disposed = true;
    await _recorder.dispose();
  }

  Future<void> _resetAfterFailure() async {
    _isRecording = false;
    try {
      await _recorder.cancel();
    } catch (_) {
      // Preserve the original recorder error.
    }
    await _clearStreamState();
  }

  Future<void> _clearStreamState() async {
    await _subscription?.cancel();
    _subscription = null;
    _streamDone = null;
    _streamError = null;
    _chunks.clear();
  }

  SpeechRecordingException _mapRecorderError(Object error) {
    if (error is SpeechRecordingException) {
      return error;
    }
    final normalized = error.toString().toLowerCase();
    if (normalized.contains('notallowed') ||
        normalized.contains('permission') ||
        normalized.contains('denied')) {
      return const SpeechRecordingException(
        'speech_permission_denied',
        'Microphone permission was denied.',
      );
    }
    if (normalized.contains('notfound') ||
        normalized.contains('no device') ||
        normalized.contains('no tracks')) {
      return const SpeechRecordingException(
        'speech_no_microphone',
        'No microphone is available.',
      );
    }
    return const SpeechRecordingException(
      'speech_recording_failed',
      'Microphone recording failed.',
    );
  }
}

Uint8List buildPcm16Wav(
  Uint8List pcm, {
  int sampleRate = RecordSpeechInputService.sampleRate,
  int numChannels = RecordSpeechInputService.numChannels,
}) {
  if (pcm.isEmpty || pcm.length.isOdd) {
    throw const SpeechRecordingException(
      'speech_no_audio',
      'PCM16 audio must contain complete samples.',
    );
  }
  if (sampleRate <= 0 || numChannels <= 0) {
    throw ArgumentError('Sample rate and channel count must be positive.');
  }
  const headerLength = 44;
  const bytesPerSample = 2;
  final output = Uint8List(headerLength + pcm.length);
  final view = ByteData.sublistView(output);

  _writeAscii(output, 0, 'RIFF');
  view.setUint32(4, 36 + pcm.length, Endian.little);
  _writeAscii(output, 8, 'WAVE');
  _writeAscii(output, 12, 'fmt ');
  view.setUint32(16, 16, Endian.little);
  view.setUint16(20, 1, Endian.little);
  view.setUint16(22, numChannels, Endian.little);
  view.setUint32(24, sampleRate, Endian.little);
  view.setUint32(28, sampleRate * numChannels * bytesPerSample, Endian.little);
  view.setUint16(32, numChannels * bytesPerSample, Endian.little);
  view.setUint16(34, bytesPerSample * 8, Endian.little);
  _writeAscii(output, 36, 'data');
  view.setUint32(40, pcm.length, Endian.little);
  output.setRange(headerLength, output.length, pcm);
  return output;
}

void _writeAscii(Uint8List target, int offset, String value) {
  for (var index = 0; index < value.length; index += 1) {
    target[offset + index] = value.codeUnitAt(index);
  }
}
