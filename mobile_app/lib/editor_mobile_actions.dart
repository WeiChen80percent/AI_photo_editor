part of 'editor_screen.dart';

mixin _EditorMobileActions on State<EditorScreen> {
  EditorController get _controller;
  ImagePicker get _imagePicker;
  late final String _serverUrl =
      widget.workspace?.baseUrl ?? ApiService.environmentBaseUrl;
  bool _mobileBusy = true;
  bool _recoveryFailed = false;
  bool _persistenceWarningShown = false;
  bool _backDialogOpen = false;
  String? _lastRemembered;

  bool get _isMobile =>
      !kIsWeb &&
      (defaultTargetPlatform == TargetPlatform.android ||
          defaultTargetPlatform == TargetPlatform.iOS);

  void _mobileNotice(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(message)));
  }

  String _mobileError(Object error) {
    final l10n = context.l10n;
    final code = error is MobileImageException
        ? error.code
        : error is ApiException
        ? error.code
        : null;
    return switch (code) {
      'image_too_large' => l10n.mobileImageTooLarge,
      'image_dimensions' => l10n.mobileImageDimensions,
      'image_format' => l10n.mobileImageFormat,
      'image_invalid' => l10n.mobileImageInvalid,
      'gallery_denied' => l10n.mobileGalleryDenied,
      'gallery_full' => l10n.mobileGalleryFull,
      'gallery_failed' => l10n.mobileGalleryFailed,
      _ =>
        error is TimeoutException
            ? l10n.mobileTimeout
            : error is ApiException
            ? l10n.mobileNetworkFailed
            : l10n.mobileOperationFailed,
    };
  }

  Future<void> _bootstrapMobile() async {
    try {
      if (widget.workspace != null) await _restoreSaved(initial: true);
      if (!mounted) return;
      if (!kIsWeb && defaultTargetPlatform == TargetPlatform.android) {
        final pending = widget.workspace?.pendingPicker;
        final recovered = await _imagePicker.retrieveLostData();
        if (!mounted) return;
        if (recovered.exception != null) {
          _mobileNotice(context.l10n.mobilePickerRecoveryFailed);
        } else if (recovered.files?.isNotEmpty == true) {
          // A reference must never silently replace the original after restart.
          if (pending?['server'] != _serverUrl ||
              !const ['original', 'reference'].contains(pending?['role'])) {
            _mobileNotice(context.l10n.mobilePickerRecoveryFailed);
          } else {
            final info = await MobileImageIO.read(recovered.files!.first);
            if (!mounted) return;
            if (pending?['role'] == 'original') {
              await _forgetWorkspace();
              if (!mounted) return;
              _controller.setOriginalImage(info.bytes);
            } else if (_controller.hasOriginal) {
              _controller.setReferenceImage(info.bytes);
            } else {
              _mobileNotice(context.l10n.mobileReferenceRecoveryFailed);
            }
          }
        }
        try {
          await widget.workspace?.endPicker();
        } catch (_) {}
      }
    } catch (error) {
      if (mounted) _mobileNotice(_mobileError(error));
    } finally {
      if (mounted) {
        setState(() => _mobileBusy = false);
        _rememberWorkspace();
      }
    }
    if (!mounted) return;
    if (widget.workspace != null && !widget.workspace!.available) {
      _mobileNotice(context.l10n.mobilePersistenceFailed);
    }
    final host = Uri.tryParse(_serverUrl)?.host;
    if (_isMobile &&
        widget.workspace != null &&
        const ['127.0.0.1', 'localhost', '::1'].contains(host)) {
      await _showConnectionSettings();
    }
  }

  void _rememberWorkspace() {
    final store = widget.workspace;
    final session = _controller.sessionId;
    if (store == null ||
        session == null ||
        _mobileBusy ||
        _controller.isRestoringSession ||
        _controller.history.isEmpty) {
      return;
    }
    final identity = '$session:${_controller.selectedEditId}';
    if (_lastRemembered == identity) return;
    _lastRemembered = identity;
    unawaited(
      store
          .rememberSession(_serverUrl, session, _controller.selectedEditId)
          .catchError((Object _) {
            if (_lastRemembered == identity) _lastRemembered = null;
            if (mounted && !_persistenceWarningShown) {
              _persistenceWarningShown = true;
              _mobileNotice(context.l10n.mobilePersistenceFailed);
            }
          }),
    );
  }

  Future<void> _forgetWorkspace() async {
    _lastRemembered = null;
    try {
      await widget.workspace?.forgetSession();
    } catch (_) {
      if (mounted) _mobileNotice(context.l10n.mobilePersistenceFailed);
    }
    if (mounted) setState(() => _recoveryFailed = false);
  }

  Future<bool> _confirmDiscard(String message) async {
    return await showDialog<bool>(
          context: context,
          builder: (dialogContext) {
            final l10n = dialogContext.l10n;
            return AlertDialog(
              title: Text(l10n.discardDraftTitle),
              content: Text(message),
              actions: [
                TextButton(
                  onPressed: () => Navigator.pop(dialogContext, false),
                  child: Text(l10n.actionCancel),
                ),
                FilledButton(
                  onPressed: () => Navigator.pop(dialogContext, true),
                  child: Text(l10n.mobileContinue),
                ),
              ],
            );
          },
        ) ==
        true;
  }

  Future<void> _restoreSaved({bool initial = false}) async {
    final saved = widget.workspace?.sessionFor(_serverUrl);
    if (saved == null || _controller.isWorkspaceBusy) return;
    if (!initial) {
      if (_mobileBusy) return;
      if ((_controller.hasOriginal || _controller.hasPendingDraft) &&
          !await _confirmDiscard(context.l10n.mobileRestoreConfirm)) {
        return;
      }
      if (!mounted) return;
      setState(() => _mobileBusy = true);
    }
    try {
      await _controller.restoreSession(
        saved['session_id'] as String,
        preferredEditId: saved['selected_edit_id'] is String
            ? saved['selected_edit_id'] as String
            : null,
      );
      if (mounted) setState(() => _recoveryFailed = false);
    } catch (error) {
      if (!mounted) return;
      setState(() => _recoveryFailed = true);
      _mobileNotice(
        error is ApiException && error.statusCode == 404
            ? context.l10n.mobileSessionMissing
            : context.l10n.mobileRestoreRetry,
      );
    } finally {
      if (!initial && mounted) {
        setState(() => _mobileBusy = false);
        _rememberWorkspace();
      }
    }
  }

  Future<void> _handleMobileAction(String action) async {
    switch (action) {
      case 'save':
        await _saveToGallery();
      case 'restore':
        await _restoreSaved();
      case 'connection':
        await _showConnectionSettings();
    }
  }

  Future<void> _saveToGallery() async {
    if (_mobileBusy ||
        _controller.isWorkspaceBusy ||
        !GalleryExport.supported) {
      return;
    }
    final selected = _controller.selectedEdit;
    if (selected == null) return;
    if (_controller.hasPendingDraft) {
      _mobileNotice(context.l10n.mobileApplyBeforeSave);
      return;
    }
    setState(() => _mobileBusy = true);
    final api = ApiService(baseUrl: _serverUrl);
    try {
      final bytes = await api.downloadResult(
        selected.resultUrl,
        maxBytes: MobileImageIO.maxBytes,
      );
      final info = await MobileImageIO.inspect(bytes);
      if (!mounted) return;
      await GalleryExport.save(info, selected.editId);
      if (mounted) {
        _mobileNotice(context.l10n.mobileSaved(info.width, info.height));
      }
    } catch (error) {
      if (mounted) _mobileNotice(_mobileError(error));
    } finally {
      api.close();
      if (mounted) setState(() => _mobileBusy = false);
    }
  }

  Future<void> _showConnectionSettings() async {
    final store = widget.workspace;
    if (store == null || _mobileBusy || _controller.isWorkspaceBusy) return;
    final nextUrl = await showDialog<String>(
      context: context,
      builder: (_) => _ServerConnectionDialog(initialUrl: _serverUrl),
    );
    if (!mounted || nextUrl == null || nextUrl == _serverUrl) return;
    if ((_controller.hasOriginal || _controller.hasPendingDraft) &&
        !await _confirmDiscard(context.l10n.mobileServerChangeConfirm)) {
      return;
    }
    if (!mounted) return;
    setState(() => _mobileBusy = true);
    try {
      await store.setServer(nextUrl);
      if (!mounted) return;
      // New controller/API owns the new server; late replies belong to the old
      // route and cannot update the new workspace or its remembered session.
      Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(builder: (_) => EditorScreen(workspace: store)),
      );
    } catch (_) {
      if (mounted) {
        setState(() => _mobileBusy = false);
        _mobileNotice(context.l10n.mobilePersistenceFailed);
      }
    }
  }

  Future<void> _cancelMobileSpeech() async {
    try {
      await _controller.cancelSpeechRecording();
    } catch (_) {
      if (mounted) _mobileNotice(context.l10n.mobileOperationFailed);
    }
  }

  Future<void> _handleMobileBack() async {
    if (_backDialogOpen) return;
    if (_controller.isSpeechBusy) {
      await _cancelMobileSpeech();
      return;
    }
    if (_mobileBusy || _controller.isWorkspaceBusy) {
      _mobileNotice(context.l10n.mobileWaitForOperation);
      return;
    }
    _backDialogOpen = true;
    try {
      if (!await _confirmDiscard(context.l10n.mobileExitConfirm) || !mounted) {
        return;
      }
      _controller.discardManualDraft();
      _controller.discardPhotoGitDraft();
      await widget.workspace?.flush();
      if (_isMobile) await SystemNavigator.pop();
    } finally {
      _backDialogOpen = false;
    }
  }
}

class _ServerConnectionDialog extends StatefulWidget {
  const _ServerConnectionDialog({required this.initialUrl});
  final String initialUrl;
  @override
  State<_ServerConnectionDialog> createState() =>
      _ServerConnectionDialogState();
}

class _ServerConnectionDialogState extends State<_ServerConnectionDialog> {
  late final TextEditingController _text = TextEditingController(
    text: widget.initialUrl,
  );
  bool _checking = false;
  String? _message;
  String? _error;
  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  String? _validated() {
    try {
      return MobileWorkspaceStore.normalizeServerUrl(_text.text);
    } on FormatException {
      setState(() => _error = context.l10n.mobileUrlInvalid);
      return null;
    }
  }

  Future<void> _check() async {
    final url = _validated();
    if (url == null) return;
    setState(() {
      _checking = true;
      _message = null;
      _error = null;
    });
    final api = ApiService(baseUrl: url);
    try {
      await api.checkConnection();
      if (mounted) setState(() => _message = context.l10n.mobileConnectionOk);
    } catch (_) {
      if (mounted) setState(() => _error = context.l10n.mobileNetworkFailed);
    } finally {
      api.close();
      if (mounted) setState(() => _checking = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    return AlertDialog(
      title: Text(l10n.mobileConnection),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(l10n.mobileConnectionHelp),
            const SizedBox(height: 16),
            TextField(
              key: const Key('server_url'),
              controller: _text,
              enabled: !_checking,
              keyboardType: TextInputType.url,
              autocorrect: false,
              enableSuggestions: false,
              onChanged: (_) => setState(() {
                _error = null;
                _message = null;
              }),
              decoration: InputDecoration(
                labelText: l10n.mobileServerAddress,
                hintText: 'http://192.168.1.100:8000',
                errorText: _error,
                errorMaxLines: 4,
              ),
            ),
            if (_checking) const LinearProgressIndicator(),
            if (_message != null)
              Padding(
                padding: const EdgeInsets.only(top: 12),
                child: Text(_message!),
              ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: _checking ? null : () => Navigator.pop(context),
          child: Text(l10n.actionCancel),
        ),
        TextButton(
          key: const Key('check_server'),
          onPressed: _checking ? null : _check,
          child: Text(l10n.mobileCheckConnection),
        ),
        FilledButton(
          key: const Key('save_server'),
          onPressed: _checking
              ? null
              : () {
                  final url = _validated();
                  if (url != null) Navigator.pop(context, url);
                },
          child: Text(l10n.mobileSaveSettings),
        ),
      ],
    );
  }
}
