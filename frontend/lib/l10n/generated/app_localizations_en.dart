// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for English (`en`).
class AppLocalizationsEn extends AppLocalizations {
  AppLocalizationsEn([String locale = 'en']) : super(locale);

  @override
  String get appTitle => 'AI Photo Editor';

  @override
  String get appCompactTitle => 'AI Editor';

  @override
  String get languageTraditionalChinese => 'Traditional Chinese';

  @override
  String get languageEnglish => 'English';

  @override
  String get switchToTraditionalChinese =>
      'Switch interface to Traditional Chinese';

  @override
  String get switchToEnglish => 'Switch interface to English';

  @override
  String get themeLight => 'Light';

  @override
  String get themeDark => 'Dark';

  @override
  String get switchToLightTheme => 'Switch to light mode';

  @override
  String get switchToDarkTheme => 'Switch to dark mode';

  @override
  String get clearCurrentWork => 'Clear current work';

  @override
  String get chooseOriginal => 'Choose original';

  @override
  String get changeOriginal => 'Change original';

  @override
  String get toolPrompt => 'Prompt';

  @override
  String get toolStyles => 'Styles';

  @override
  String get toolReference => 'Reference';

  @override
  String get toolManual => 'Adjust';

  @override
  String get toolHistory => 'History';

  @override
  String get labelOriginal => 'Original';

  @override
  String get labelCompare => 'Compare';

  @override
  String get labelResult => 'Result';

  @override
  String get labelPreview => 'Preview';

  @override
  String get labelBefore => 'Before';

  @override
  String get labelAfter => 'After';

  @override
  String get comparisonBaseline => 'Compare with';

  @override
  String get comparisonBaselineOriginal => 'Original';

  @override
  String get comparisonBaselineParent => 'Previous edit';

  @override
  String get comparisonParentUnavailable =>
      'This version has no available previous edit. Comparing with the original instead.';

  @override
  String get comparisonDragHandle => 'Before and after split';

  @override
  String comparisonDragHandleValue(int percent) {
    return 'Before and after split at $percent%';
  }

  @override
  String get comparisonMoveLeft => 'Show more of the result';

  @override
  String get comparisonMoveRight => 'Show more of the comparison image';

  @override
  String get resetZoom => 'Reset view';

  @override
  String get holdToSeeOriginal =>
      'Press and hold the photo to see the original';

  @override
  String get dismissHint => 'Dismiss hint';

  @override
  String get selectPhotoToStart => 'Choose a photo to start';

  @override
  String get photoWorkspaceDescription =>
      'Your photo stays fully visible, and edited results and history remain available.';

  @override
  String get photoWorkspaceCompactDescription =>
      'Your photo, results, and history remain available.';

  @override
  String get selectOriginal => 'Choose original photo';

  @override
  String get resultAppearsHere => 'Your result will appear here after an edit';

  @override
  String get noImage => 'No image';

  @override
  String get processing => 'Processing…';

  @override
  String get imageLoadFailed => 'Could not load image';

  @override
  String get discardDraftTitle => 'Discard unapplied adjustments?';

  @override
  String get discardDraftForHistory =>
      'Switching history versions will discard the current manual adjustment draft.';

  @override
  String get discardDraftForOriginal =>
      'Returning to the original to create a new branch will discard the current manual adjustment draft.';

  @override
  String get discardPhotoGitForTool =>
      'Opening another tool will discard the current version operation and its preview.';

  @override
  String get actionBack => 'Back';

  @override
  String get actionDiscardAndSwitch => 'Discard and switch';

  @override
  String get replaceOriginalTitle => 'Change original photo?';

  @override
  String get replaceOriginalMessage =>
      'Changing it clears the current session, unapplied manual draft, and unfinished version operation.';

  @override
  String get actionCancel => 'Cancel';

  @override
  String get actionReplaceImage => 'Change photo';

  @override
  String imagePickFailed(String error) {
    return 'Could not choose image: $error';
  }

  @override
  String get clearWorkTitle => 'Clear current work?';

  @override
  String get clearWorkMessage =>
      'The screen returns to its initial state and unfinished drafts are discarded. History already saved by the backend is not deleted.';

  @override
  String get actionClearScreen => 'Clear screen';

  @override
  String get promptEditTitle => 'Prompt edit';

  @override
  String get promptBranchFromOriginal =>
      'Create a new history branch from the original';

  @override
  String get promptFirstVersionFromOriginal =>
      'Create the first version from the original';

  @override
  String get promptContinueSelected =>
      'Continue editing from the selected version';

  @override
  String get promptHint =>
      'For example: increase brightness by ten, apply Cinematic at 100%, or merge version 4 and version 6';

  @override
  String get promptModeNotice =>
      'Type or speak one action. Apply routes it to editing, exact parameters, styles, or version tools.';

  @override
  String get commandPlanning => 'Understanding command…';

  @override
  String get commandPlanTitle => 'Command plan';

  @override
  String get commandPreviewNotice =>
      'Version operations always require a preview and your confirmation before a new version is created.';

  @override
  String get speechLanguageLabel => 'Recognition language';

  @override
  String get speechLanguageHelp =>
      'Choose Chinese or English for short commands; use automatic for mixed speech.';

  @override
  String get speechLanguageTraditionalChinese => 'Traditional Chinese';

  @override
  String get speechLanguageEnglish => 'English';

  @override
  String get speechLanguageAutomatic => 'Automatic detection';

  @override
  String speechResultMetadata(String language, String model) {
    return 'Recognized as $language · $model';
  }

  @override
  String get speechStart => 'Use microphone';

  @override
  String get speechStop => 'Stop';

  @override
  String get speechCancel => 'Cancel';

  @override
  String get speechRequestingPermission => 'Requesting microphone permission…';

  @override
  String speechRecordingSeconds(int seconds) {
    return 'Recording · ${seconds}s';
  }

  @override
  String get speechTranscribing => 'Turning speech into editable text…';

  @override
  String get speechPrivacyNotice =>
      'Audio is processed by the local backend and is not saved to edit history.';

  @override
  String get speechUnavailable =>
      'Microphone input is unavailable here. You can still type a prompt.';

  @override
  String get statusSpeechCompleted =>
      'Speech was added as editable text. Review it before applying.';

  @override
  String get statusSpeechCancelled => 'Voice input was cancelled.';

  @override
  String get errorSpeechPermissionDenied =>
      'Microphone permission was denied. Allow it in Chrome settings or type the prompt instead.';

  @override
  String get errorSpeechNoMicrophone =>
      'No usable microphone was found. Check the device or type the prompt instead.';

  @override
  String get errorSpeechRecorderUnavailable =>
      'This browser cannot provide the required microphone format. Use current Chrome or type the prompt instead.';

  @override
  String get errorSpeechRecordingFailed =>
      'Recording failed. Check the microphone and try again.';

  @override
  String get errorSpeechNoAudio =>
      'The microphone returned no usable audio. Please record again.';

  @override
  String get errorSpeechInvalidAudio =>
      'The recording could not be read. Please record again.';

  @override
  String get errorSpeechUnsupportedFormat =>
      'This recording format is not supported. Please record again in Chrome.';

  @override
  String get errorSpeechNoSpeech =>
      'No usable speech was detected. Move closer to the microphone and try again.';

  @override
  String get errorSpeechTooLong =>
      'The recording is longer than 15 seconds. Please use a shorter editing prompt.';

  @override
  String get errorSpeechTooLarge =>
      'The recording is too large. Please use a shorter editing prompt.';

  @override
  String get errorSpeechModelUnavailable =>
      'The local speech model is unavailable. Check the backend model and device settings.';

  @override
  String get errorSpeechTranscriptionFailed =>
      'Speech recognition failed. Please record again.';

  @override
  String get errorSpeechTimeout =>
      'Speech recognition took too long. Please try again.';

  @override
  String get errorSpeechBackendUnavailable =>
      'Could not connect to the local speech backend. Typing is still available.';

  @override
  String get applyPrompt => 'Apply prompt';

  @override
  String get styleCatalogTitle => 'Style catalog';

  @override
  String get styleCatalogUnavailable =>
      'The style catalog is unavailable. Check that the backend is running.';

  @override
  String styleCatalogSubtitle(int count, String version) {
    return '$count approved styles · v$version';
  }

  @override
  String get styleCategoryPrevious => 'See previous style categories';

  @override
  String get styleCategoryNext => 'See more style categories';

  @override
  String get styleCategoryAll => 'All';

  @override
  String get styleStrength => 'Strength';

  @override
  String get applyStyle => 'Apply style';

  @override
  String get referenceEditTitle => 'Reference edit';

  @override
  String get referenceFromOriginal =>
      'Adjust the original toward the reference image colors';

  @override
  String get referenceFromCurrent =>
      'Apply the reference direction to the current version';

  @override
  String get selectReference => 'Choose reference';

  @override
  String get changeReference => 'Change reference';

  @override
  String get removeReference => 'Remove reference';

  @override
  String get applyReference => 'Apply reference';

  @override
  String get manualEditTitle => 'Manual adjustments';

  @override
  String manualSourceVersion(String target, String mode) {
    return 'Source · $target · $mode';
  }

  @override
  String get advancedAdjustments => 'Advanced adjustments';

  @override
  String get historyTitle => 'History';

  @override
  String historyVersionCount(int count) {
    return '$count versions';
  }

  @override
  String get refreshHistory => 'Sync history';

  @override
  String get selectedOriginalNewBranch =>
      'Original selected · next edit creates a new branch';

  @override
  String get createBranchFromOriginal =>
      'Create a new branch from the original';

  @override
  String get emptyHistory => 'Versions appear here after your first edit.';

  @override
  String get currentPreview => 'Current preview';

  @override
  String get currentAdjustments => 'Current adjustments';

  @override
  String styleEffectiveParameters(int strength) {
    return 'Equivalent parameters at $strength% strength. The style also uses curves, split toning, and other internal recipes.';
  }

  @override
  String get noManualParameters =>
      'This version has no manual parameters to display.';

  @override
  String styleUnderstanding(String name, int strength) {
    return 'Interpretation: applied $name at $strength% strength.';
  }

  @override
  String adjustmentCount(int count) {
    return '$count adjustments';
  }

  @override
  String get adaptiveIntervalReset => 'Range reset';

  @override
  String get adaptiveConverged => 'Converged';

  @override
  String get adaptiveContinue => 'Continuing fine-tune';

  @override
  String get adaptiveFineTune => 'Adaptive fine-tune';

  @override
  String get relativeAdjustment => 'Relative change';

  @override
  String get candidateValue => 'Candidate value';

  @override
  String get currentBounds => 'Current bounds';

  @override
  String get stepSize => 'Step';

  @override
  String stepSizeWithTransform(String transform) {
    return 'Step ($transform)';
  }

  @override
  String get adaptiveReasonInitial => 'Create the initial step';

  @override
  String get adaptiveReasonReverse => 'Move back from the current effect';

  @override
  String get adaptiveReasonHandoff => 'Continue with a related parameter';

  @override
  String get adaptiveReasonMidpoint =>
      'Use the interval midpoint based on feedback';

  @override
  String get adaptiveReasonContinue =>
      'Continue exploring in the same direction';

  @override
  String get adaptiveReasonNarrow => 'Narrow the range after opposite feedback';

  @override
  String get adaptiveReasonReanchor => 'Rebuild the adjustment baseline';

  @override
  String get adaptiveReasonAbsolute =>
      'Use an explicit value and reset the range';

  @override
  String get adaptiveReasonRelative => 'Apply a relative numeric change';

  @override
  String get adaptiveReasonResetAxis => 'Reset one parameter';

  @override
  String get adaptiveReasonResetOriginal => 'Return to the original';

  @override
  String get collapse => 'Collapse';

  @override
  String resetParameter(String label) {
    return 'Reset $label to neutral';
  }

  @override
  String equivalentParameters(String summary) {
    return 'Equivalent $summary';
  }

  @override
  String historyVersionMode(int version, String mode) {
    return 'Version $version · $mode';
  }

  @override
  String get rootBranch => 'Root branch';

  @override
  String get continuesParent => 'Continues parent version';

  @override
  String continuesVersion(int version) {
    return 'Continues version $version';
  }

  @override
  String get referenceNotSelected => 'No reference selected';

  @override
  String get actionReset => 'Reset';

  @override
  String get actionApply => 'Apply';

  @override
  String get actionApplying => 'Applying…';

  @override
  String get notApplied => 'Not applied';

  @override
  String get parameterExposure => 'Exposure';

  @override
  String get parameterBrightness => 'Brightness';

  @override
  String get parameterContrast => 'Contrast';

  @override
  String get parameterHighlights => 'Highlights';

  @override
  String get parameterShadows => 'Shadows';

  @override
  String get parameterWhites => 'Whites';

  @override
  String get parameterBlacks => 'Blacks';

  @override
  String get parameterSaturation => 'Saturation';

  @override
  String get parameterVibrance => 'Vibrance';

  @override
  String get parameterTemperature => 'Temperature';

  @override
  String get parameterWhiteBalanceTint => 'White balance tint';

  @override
  String get parameterSharpen => 'Sharpen';

  @override
  String get parameterClarity => 'Clarity';

  @override
  String get parameterDehaze => 'Dehaze';

  @override
  String get parameterVignette => 'Vignette';

  @override
  String get regionAll => 'Whole image';

  @override
  String get regionSky => 'Sky';

  @override
  String get regionPerson => 'Person';

  @override
  String get regionBackground => 'Background';

  @override
  String get regionHighlights => 'Bright areas';

  @override
  String get regionShadows => 'Dark areas';

  @override
  String get regionCenter => 'Center';

  @override
  String get regionEdges => 'Edges';

  @override
  String get modePrompt => 'Prompt';

  @override
  String get modeStyle => 'Style';

  @override
  String get modeReference => 'Reference';

  @override
  String get modeManual => 'Manual';

  @override
  String get promptEditFallbackTitle => 'Prompt edit';

  @override
  String get referenceEditDisplayTitle => 'Reference edit';

  @override
  String get manualEditDisplayTitle => 'Manual adjustments';

  @override
  String get parserLlm => 'LLM interpretation';

  @override
  String get parserRules => 'Rule-based interpretation';

  @override
  String get parserReference => 'Reference mode';

  @override
  String get parserManual => 'Manual parameters';

  @override
  String get styleFamilyNaturalClean => 'Natural & clean';

  @override
  String get styleFamilyPortraitSkin => 'Portrait skin';

  @override
  String get styleFamilyLandscapeTravel => 'Landscape & travel';

  @override
  String get styleFamilyCinematic => 'Cinematic';

  @override
  String get styleFamilyFilmRetro => 'Film & retro';

  @override
  String get styleFamilyBlackWhite => 'Black & white';

  @override
  String get styleFamilyNightNeon => 'Night & neon';

  @override
  String get styleFamilyPastelCreative => 'Pastel & creative';

  @override
  String get summaryOriginalNewBranch =>
      'Original · next edit creates a new branch';

  @override
  String get summaryChoosePhoto =>
      'Choose a photo, then start with a prompt or reference image';

  @override
  String get summaryPreviewPrefix => 'Preview · ';

  @override
  String get manualUnavailableNeedPrompt =>
      'Complete a prompt edit before opening manual adjustments.';

  @override
  String get manualUnavailableReference =>
      'Reference results cannot be manually adjusted yet. Select a prompt or manual version first.';

  @override
  String get manualUnavailableEngine =>
      'The first manual adjustment version only supports OpenCV results.';

  @override
  String get manualUnavailableGeneric =>
      'This version does not support manual adjustments.';

  @override
  String get statusSelectedNewOriginal => 'New original photo selected';

  @override
  String get statusReferenceReady => 'Reference image ready';

  @override
  String get errorPromptRequired => 'Enter an editing prompt.';

  @override
  String get errorReferenceRequired => 'Choose a reference image first.';

  @override
  String errorStyleCatalogLoad(String error) {
    return 'Could not load style catalog: $error';
  }

  @override
  String get errorOriginalRequired => 'Choose an original photo first.';

  @override
  String get statusParsingPrompt => 'Interpreting edit prompt…';

  @override
  String get statusApplyingReference => 'Applying reference image…';

  @override
  String get statusEditComplete => 'Edit complete';

  @override
  String errorEditFailed(String error) {
    return 'Edit failed: $error';
  }

  @override
  String get statusHistorySynced => 'History synced';

  @override
  String get statusSwitchedHistory => 'Switched to history version';

  @override
  String get statusSwitchedOriginal =>
      'Switched to original. A new history branch can now be created.';

  @override
  String errorOpenManual(String error) {
    return 'Could not open manual adjustments: $error';
  }

  @override
  String get statusResetSourceParameters =>
      'Restored source version parameters';

  @override
  String errorManualPreview(String error) {
    return 'Manual preview failed: $error';
  }

  @override
  String get statusApplyingManual => 'Applying manual adjustments…';

  @override
  String get statusManualCommitted =>
      'Manual adjustments applied and added to history';

  @override
  String errorManualCommit(String error) {
    return 'Could not apply manual adjustments: $error';
  }

  @override
  String get errorStyleAmbiguous =>
      'This description matches multiple styles. Choose a specific style from the catalog.';

  @override
  String get errorStyleCompound =>
      'Apply a style first, then adjust brightness, color, or other parameters in a follow-up prompt.';

  @override
  String get errorStyleAsset =>
      'Style asset or version validation failed. No substitute style was applied.';

  @override
  String get errorSemanticTargetNotFound =>
      'The requested area was not found in this photo. Try another photo or edit the whole image.';

  @override
  String get errorAdaptiveClarification =>
      'I am not sure which setting to fine-tune. Name a parameter or region.';

  @override
  String get errorAdaptiveConverged =>
      'This adjustment is near the minimum step. Use manual parameters for a final fine-tune.';

  @override
  String get errorAdaptiveSatisfied =>
      'The current result was kept. No duplicate history version was added.';

  @override
  String get errorManualSourceUnsupported =>
      'Reference results cannot be manually adjusted yet. Select a prompt or manual version.';

  @override
  String get errorBackendUnavailable =>
      'Could not connect to the editing backend. Check that it is running.';

  @override
  String get errorCheckPrompt => 'Review the editing prompt and try again.';

  @override
  String adaptiveIssuesContext(String message, String contexts) {
    return '$message (involves: $contexts)';
  }

  @override
  String networkBackendError(String error) {
    return 'Could not connect to the editing backend: $error';
  }

  @override
  String backendHttpError(int statusCode) {
    return 'Backend request failed (HTTP $statusCode)';
  }

  @override
  String get backendInvalidResponse =>
      'The backend returned an unrecognized data format.';

  @override
  String get photoGitTitle => 'Version operations';

  @override
  String get photoGitSubtitle => 'Merge or selectively undo tracked edits';

  @override
  String get photoGitUnavailable =>
      'Select an OpenCV history version to use version operations.';

  @override
  String get photoGitManualDraftBlocked =>
      'Finish or discard the manual adjustment draft before starting a version operation.';

  @override
  String get photoGitMerge => 'Merge versions';

  @override
  String get photoGitSelectiveRevert => 'Selective undo';

  @override
  String get photoGitDeterministic => 'Deterministic version plan';

  @override
  String get photoGitTarget => 'Target';

  @override
  String get photoGitSource => 'Source';

  @override
  String get photoGitRevertStep => 'Step to undo';

  @override
  String get photoGitChooseSource => 'Choose another version';

  @override
  String get photoGitChooseRevertStep =>
      'Choose a step from the target lineage';

  @override
  String get photoGitInstruction => 'Scope description';

  @override
  String get photoGitMergeHint =>
      'For example: bring in only the sky saturation';

  @override
  String get photoGitRevertHint =>
      'For example: undo only this step\'s saturation';

  @override
  String get photoGitScopeAssist => 'Optional scope shortcuts';

  @override
  String get photoGitAnyRegion => 'Any region';

  @override
  String get photoGitAnyParameter => 'Any parameter';

  @override
  String get photoGitAnalyze => 'Analyze changes';

  @override
  String get photoGitAnalyzing => 'Analyzing…';

  @override
  String get photoGitPlanSummary => 'Plan summary';

  @override
  String get photoGitAdded => 'Added';

  @override
  String get photoGitRemoved => 'Removed';

  @override
  String get photoGitConflicts => 'Conflicts';

  @override
  String get photoGitNoContribution => 'No matching tracked change.';

  @override
  String get photoGitConflictHelp =>
      'Resolve every conflict before previewing.';

  @override
  String get photoGitKeepTarget => 'Keep target';

  @override
  String get photoGitUseSource => 'Use source';

  @override
  String get photoGitReplayLater => 'Undo and replay later edits';

  @override
  String get photoGitPreview => 'Create preview';

  @override
  String get photoGitPreviewing => 'Rendering preview…';

  @override
  String get photoGitCommit => 'Create version';

  @override
  String get photoGitCommitting => 'Creating version…';

  @override
  String get photoGitCancel => 'Cancel operation';

  @override
  String get photoGitMergedFrom => 'Merged from';

  @override
  String get photoGitRevertedFrom => 'Undid effects from';

  @override
  String get photoGitCommonAncestor => 'Common ancestor';

  @override
  String get photoGitSchema => 'Recipe version';

  @override
  String get photoGitPlanHash => 'Plan';

  @override
  String get photoGitResolutions => 'Conflict decisions';

  @override
  String get photoGitTargetValue => 'Target value';

  @override
  String get photoGitSourceValue => 'Source value';

  @override
  String get photoGitLaterChanges => 'Later edits';

  @override
  String get statusPhotoGitPlanning => 'Analyzing version differences…';

  @override
  String get statusPhotoGitConflictsFound =>
      'Conflicts found. Choose a result for each one.';

  @override
  String get statusPhotoGitNoChange =>
      'The selected scope would not change the target.';

  @override
  String get statusPhotoGitPlanReady => 'The version plan is ready.';

  @override
  String get statusPhotoGitPreviewing => 'Rendering the version preview…';

  @override
  String get statusPhotoGitPreviewReady =>
      'Preview ready. Compare it before creating the version.';

  @override
  String get statusPhotoGitCommitting => 'Creating a tracked version…';

  @override
  String get statusPhotoGitCommitted => 'The version was added to history.';

  @override
  String get errorPhotoGitRequestIncomplete =>
      'Choose a version and specify a region or parameter.';

  @override
  String errorPhotoGitPlan(String error) {
    return 'Version analysis failed: $error';
  }

  @override
  String errorPhotoGitPreview(String error) {
    return 'Version preview failed: $error';
  }

  @override
  String errorPhotoGitCommit(String error) {
    return 'Could not create the version: $error';
  }

  @override
  String get errorPhotoGitScope =>
      'Specify a supported region or parameter in text or with a shortcut.';

  @override
  String get errorPhotoGitConflict =>
      'Some conflicts are unresolved. Choose each result and try again.';

  @override
  String get errorPhotoGitStale =>
      'The version changed. Analyze it again before previewing.';

  @override
  String get errorPhotoGitNoChange =>
      'The selected content matches the target, so no duplicate version was created.';

  @override
  String get errorPhotoGitUnsupported =>
      'This version does not contain enough tracked information for a safe operation.';

  @override
  String get errorPhotoGitDraftActive =>
      'Finish or cancel the current version operation first.';

  @override
  String contractBadgePassed(int passed, int total) {
    return 'Contract $passed/$total passed';
  }

  @override
  String contractBadgeAdjusted(int scale) {
    return 'Adjusted to $scale% to meet the contract';
  }

  @override
  String get contractDetailsTitle => 'Verified edit contract';

  @override
  String get contractStatusPassed => 'Passed at requested strength';

  @override
  String get contractStatusAdjusted =>
      'Passed after a safe strength adjustment';

  @override
  String get contractChecks => 'Verification checks';

  @override
  String get contractConstraints => 'Understood constraints';

  @override
  String get contractRequestedScale => 'Requested strength';

  @override
  String get contractAppliedScale => 'Applied strength';

  @override
  String get contractThreshold => 'Threshold';

  @override
  String get contractThresholdSource => 'Threshold source';

  @override
  String get contractBaseline => 'Baseline';

  @override
  String get contractActual => 'Actual';

  @override
  String get contractMetricVersion => 'Metric version';

  @override
  String get contractTargetVersion => 'Verified target';

  @override
  String get contractParentVersion => 'Parent';

  @override
  String get contractVerificationTime => 'Verification time';

  @override
  String get contractVersions => 'Contract versions';

  @override
  String get contractRequestedParameters => 'Requested parameters';

  @override
  String get contractActualParameters => 'Applied parameters';

  @override
  String get contractPolicyDefault => 'Versioned policy default';

  @override
  String get contractExplicitUser => 'Specified by user';

  @override
  String get contractSystemPolicy => 'System safety policy';

  @override
  String get contractOperatorAtMost => 'At most';

  @override
  String get contractOperatorNoWorse => 'No worse than baseline';

  @override
  String get contractCheckPassed => 'Passed';

  @override
  String get contractCheckFailed => 'Failed';

  @override
  String get contractUnknownMetric => 'Unknown metric';

  @override
  String get contractNoChecks => 'No verification check data was returned.';

  @override
  String contractMilliseconds(String value) {
    return '$value ms';
  }

  @override
  String get errorContractClarification =>
      'One or more protection conditions are unclear. Clarify the metric, area, or limit and try again.';

  @override
  String get errorContractUnsupported =>
      'This protection condition or required photo area cannot be verified yet. No edit was applied.';

  @override
  String get errorContractUnsatisfied =>
      'No effective edit could satisfy every protection condition. Adjust the limit and try again.';

  @override
  String get errorContractNoChange =>
      'The safe result would not create a visible change, so no duplicate version was added.';

  @override
  String get errorContractConflict =>
      'This request ID was already used for different edit content. Submit the current prompt again.';

  @override
  String get errorContractSchema =>
      'Contract display metadata could not be loaded. Metric identifiers remain available.';
}
