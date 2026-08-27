import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:intl/intl.dart' as intl;

import 'app_localizations_en.dart';
import 'app_localizations_zh.dart';

// ignore_for_file: type=lint

/// Callers can lookup localized strings with an instance of AppLocalizations
/// returned by `AppLocalizations.of(context)`.
///
/// Applications need to include `AppLocalizations.delegate()` in their app's
/// `localizationDelegates` list, and the locales they support in the app's
/// `supportedLocales` list. For example:
///
/// ```dart
/// import 'generated/app_localizations.dart';
///
/// return MaterialApp(
///   localizationsDelegates: AppLocalizations.localizationsDelegates,
///   supportedLocales: AppLocalizations.supportedLocales,
///   home: MyApplicationHome(),
/// );
/// ```
///
/// ## Update pubspec.yaml
///
/// Please make sure to update your pubspec.yaml to include the following
/// packages:
///
/// ```yaml
/// dependencies:
///   # Internationalization support.
///   flutter_localizations:
///     sdk: flutter
///   intl: any # Use the pinned version from flutter_localizations
///
///   # Rest of dependencies
/// ```
///
/// ## iOS Applications
///
/// iOS applications define key application metadata, including supported
/// locales, in an Info.plist file that is built into the application bundle.
/// To configure the locales supported by your app, you’ll need to edit this
/// file.
///
/// First, open your project’s ios/Runner.xcworkspace Xcode workspace file.
/// Then, in the Project Navigator, open the Info.plist file under the Runner
/// project’s Runner folder.
///
/// Next, select the Information Property List item, select Add Item from the
/// Editor menu, then select Localizations from the pop-up menu.
///
/// Select and expand the newly-created Localizations item then, for each
/// locale your application supports, add a new item and select the locale
/// you wish to add from the pop-up menu in the Value field. This list should
/// be consistent with the languages listed in the AppLocalizations.supportedLocales
/// property.
abstract class AppLocalizations {
  AppLocalizations(String locale)
    : localeName = intl.Intl.canonicalizedLocale(locale.toString());

  final String localeName;

  static AppLocalizations of(BuildContext context) {
    return Localizations.of<AppLocalizations>(context, AppLocalizations)!;
  }

  static const LocalizationsDelegate<AppLocalizations> delegate =
      _AppLocalizationsDelegate();

  /// A list of this localizations delegate along with the default localizations
  /// delegates.
  ///
  /// Returns a list of localizations delegates containing this delegate along with
  /// GlobalMaterialLocalizations.delegate, GlobalCupertinoLocalizations.delegate,
  /// and GlobalWidgetsLocalizations.delegate.
  ///
  /// Additional delegates can be added by appending to this list in
  /// MaterialApp. This list does not have to be used at all if a custom list
  /// of delegates is preferred or required.
  static const List<LocalizationsDelegate<dynamic>> localizationsDelegates =
      <LocalizationsDelegate<dynamic>>[
        delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
      ];

  /// A list of this localizations delegate's supported locales.
  static const List<Locale> supportedLocales = <Locale>[
    Locale('en'),
    Locale('zh'),
    Locale('zh', 'TW'),
  ];

  /// No description provided for @appTitle.
  ///
  /// In en, this message translates to:
  /// **'AI Photo Editor'**
  String get appTitle;

  /// No description provided for @appCompactTitle.
  ///
  /// In en, this message translates to:
  /// **'AI Editor'**
  String get appCompactTitle;

  /// No description provided for @languageTraditionalChinese.
  ///
  /// In en, this message translates to:
  /// **'Traditional Chinese'**
  String get languageTraditionalChinese;

  /// No description provided for @languageEnglish.
  ///
  /// In en, this message translates to:
  /// **'English'**
  String get languageEnglish;

  /// No description provided for @switchToTraditionalChinese.
  ///
  /// In en, this message translates to:
  /// **'Switch interface to Traditional Chinese'**
  String get switchToTraditionalChinese;

  /// No description provided for @switchToEnglish.
  ///
  /// In en, this message translates to:
  /// **'Switch interface to English'**
  String get switchToEnglish;

  /// No description provided for @themeLight.
  ///
  /// In en, this message translates to:
  /// **'Light'**
  String get themeLight;

  /// No description provided for @themeDark.
  ///
  /// In en, this message translates to:
  /// **'Dark'**
  String get themeDark;

  /// No description provided for @switchToLightTheme.
  ///
  /// In en, this message translates to:
  /// **'Switch to light mode'**
  String get switchToLightTheme;

  /// No description provided for @switchToDarkTheme.
  ///
  /// In en, this message translates to:
  /// **'Switch to dark mode'**
  String get switchToDarkTheme;

  /// No description provided for @clearCurrentWork.
  ///
  /// In en, this message translates to:
  /// **'Clear current work'**
  String get clearCurrentWork;

  /// No description provided for @chooseOriginal.
  ///
  /// In en, this message translates to:
  /// **'Choose original'**
  String get chooseOriginal;

  /// No description provided for @changeOriginal.
  ///
  /// In en, this message translates to:
  /// **'Change original'**
  String get changeOriginal;

  /// No description provided for @toolPrompt.
  ///
  /// In en, this message translates to:
  /// **'Prompt'**
  String get toolPrompt;

  /// No description provided for @toolStyles.
  ///
  /// In en, this message translates to:
  /// **'Styles'**
  String get toolStyles;

  /// No description provided for @toolReference.
  ///
  /// In en, this message translates to:
  /// **'Reference'**
  String get toolReference;

  /// No description provided for @toolManual.
  ///
  /// In en, this message translates to:
  /// **'Adjust'**
  String get toolManual;

  /// No description provided for @toolHistory.
  ///
  /// In en, this message translates to:
  /// **'History'**
  String get toolHistory;

  /// No description provided for @labelOriginal.
  ///
  /// In en, this message translates to:
  /// **'Original'**
  String get labelOriginal;

  /// No description provided for @labelCompare.
  ///
  /// In en, this message translates to:
  /// **'Compare'**
  String get labelCompare;

  /// No description provided for @labelResult.
  ///
  /// In en, this message translates to:
  /// **'Result'**
  String get labelResult;

  /// No description provided for @labelPreview.
  ///
  /// In en, this message translates to:
  /// **'Preview'**
  String get labelPreview;

  /// No description provided for @labelBefore.
  ///
  /// In en, this message translates to:
  /// **'Before'**
  String get labelBefore;

  /// No description provided for @labelAfter.
  ///
  /// In en, this message translates to:
  /// **'After'**
  String get labelAfter;

  /// No description provided for @comparisonBaseline.
  ///
  /// In en, this message translates to:
  /// **'Compare with'**
  String get comparisonBaseline;

  /// No description provided for @comparisonBaselineOriginal.
  ///
  /// In en, this message translates to:
  /// **'Original'**
  String get comparisonBaselineOriginal;

  /// No description provided for @comparisonBaselineParent.
  ///
  /// In en, this message translates to:
  /// **'Previous edit'**
  String get comparisonBaselineParent;

  /// No description provided for @comparisonParentUnavailable.
  ///
  /// In en, this message translates to:
  /// **'This version has no available previous edit. Comparing with the original instead.'**
  String get comparisonParentUnavailable;

  /// No description provided for @comparisonDragHandle.
  ///
  /// In en, this message translates to:
  /// **'Before and after split'**
  String get comparisonDragHandle;

  /// No description provided for @comparisonDragHandleValue.
  ///
  /// In en, this message translates to:
  /// **'Before and after split at {percent}%'**
  String comparisonDragHandleValue(int percent);

  /// No description provided for @comparisonMoveLeft.
  ///
  /// In en, this message translates to:
  /// **'Show more of the result'**
  String get comparisonMoveLeft;

  /// No description provided for @comparisonMoveRight.
  ///
  /// In en, this message translates to:
  /// **'Show more of the comparison image'**
  String get comparisonMoveRight;

  /// No description provided for @resetZoom.
  ///
  /// In en, this message translates to:
  /// **'Reset view'**
  String get resetZoom;

  /// No description provided for @holdToSeeOriginal.
  ///
  /// In en, this message translates to:
  /// **'Press and hold the photo to see the original'**
  String get holdToSeeOriginal;

  /// No description provided for @dismissHint.
  ///
  /// In en, this message translates to:
  /// **'Dismiss hint'**
  String get dismissHint;

  /// No description provided for @selectPhotoToStart.
  ///
  /// In en, this message translates to:
  /// **'Choose a photo to start'**
  String get selectPhotoToStart;

  /// No description provided for @photoWorkspaceDescription.
  ///
  /// In en, this message translates to:
  /// **'Your photo stays fully visible, and edited results and history remain available.'**
  String get photoWorkspaceDescription;

  /// No description provided for @photoWorkspaceCompactDescription.
  ///
  /// In en, this message translates to:
  /// **'Your photo, results, and history remain available.'**
  String get photoWorkspaceCompactDescription;

  /// No description provided for @selectOriginal.
  ///
  /// In en, this message translates to:
  /// **'Choose original photo'**
  String get selectOriginal;

  /// No description provided for @resultAppearsHere.
  ///
  /// In en, this message translates to:
  /// **'Your result will appear here after an edit'**
  String get resultAppearsHere;

  /// No description provided for @noImage.
  ///
  /// In en, this message translates to:
  /// **'No image'**
  String get noImage;

  /// No description provided for @processing.
  ///
  /// In en, this message translates to:
  /// **'Processing…'**
  String get processing;

  /// No description provided for @imageLoadFailed.
  ///
  /// In en, this message translates to:
  /// **'Could not load image'**
  String get imageLoadFailed;

  /// No description provided for @discardDraftTitle.
  ///
  /// In en, this message translates to:
  /// **'Discard unapplied adjustments?'**
  String get discardDraftTitle;

  /// No description provided for @discardDraftForHistory.
  ///
  /// In en, this message translates to:
  /// **'Switching history versions will discard the current manual adjustment draft.'**
  String get discardDraftForHistory;

  /// No description provided for @discardDraftForOriginal.
  ///
  /// In en, this message translates to:
  /// **'Returning to the original to create a new branch will discard the current manual adjustment draft.'**
  String get discardDraftForOriginal;

  /// No description provided for @discardPhotoGitForTool.
  ///
  /// In en, this message translates to:
  /// **'Opening another tool will discard the current version operation and its preview.'**
  String get discardPhotoGitForTool;

  /// No description provided for @actionBack.
  ///
  /// In en, this message translates to:
  /// **'Back'**
  String get actionBack;

  /// No description provided for @actionDiscardAndSwitch.
  ///
  /// In en, this message translates to:
  /// **'Discard and switch'**
  String get actionDiscardAndSwitch;

  /// No description provided for @replaceOriginalTitle.
  ///
  /// In en, this message translates to:
  /// **'Change original photo?'**
  String get replaceOriginalTitle;

  /// No description provided for @replaceOriginalMessage.
  ///
  /// In en, this message translates to:
  /// **'Changing it clears the current session, unapplied manual draft, and unfinished version operation.'**
  String get replaceOriginalMessage;

  /// No description provided for @actionCancel.
  ///
  /// In en, this message translates to:
  /// **'Cancel'**
  String get actionCancel;

  /// No description provided for @actionReplaceImage.
  ///
  /// In en, this message translates to:
  /// **'Change photo'**
  String get actionReplaceImage;

  /// No description provided for @imagePickFailed.
  ///
  /// In en, this message translates to:
  /// **'Could not choose image: {error}'**
  String imagePickFailed(String error);

  /// No description provided for @clearWorkTitle.
  ///
  /// In en, this message translates to:
  /// **'Clear current work?'**
  String get clearWorkTitle;

  /// No description provided for @clearWorkMessage.
  ///
  /// In en, this message translates to:
  /// **'The screen returns to its initial state and unfinished drafts are discarded. History already saved by the backend is not deleted.'**
  String get clearWorkMessage;

  /// No description provided for @actionClearScreen.
  ///
  /// In en, this message translates to:
  /// **'Clear screen'**
  String get actionClearScreen;

  /// No description provided for @promptEditTitle.
  ///
  /// In en, this message translates to:
  /// **'Prompt edit'**
  String get promptEditTitle;

  /// No description provided for @promptBranchFromOriginal.
  ///
  /// In en, this message translates to:
  /// **'Create a new history branch from the original'**
  String get promptBranchFromOriginal;

  /// No description provided for @promptFirstVersionFromOriginal.
  ///
  /// In en, this message translates to:
  /// **'Create the first version from the original'**
  String get promptFirstVersionFromOriginal;

  /// No description provided for @promptContinueSelected.
  ///
  /// In en, this message translates to:
  /// **'Continue editing from the selected version'**
  String get promptContinueSelected;

  /// No description provided for @promptHint.
  ///
  /// In en, this message translates to:
  /// **'For example: increase brightness by ten, apply Cinematic at 100%, or merge version 4 and version 6'**
  String get promptHint;

  /// No description provided for @promptModeNotice.
  ///
  /// In en, this message translates to:
  /// **'Type or speak one action. Apply routes it to editing, exact parameters, styles, or version tools.'**
  String get promptModeNotice;

  /// No description provided for @commandPlanning.
  ///
  /// In en, this message translates to:
  /// **'Understanding command…'**
  String get commandPlanning;

  /// No description provided for @commandPlanTitle.
  ///
  /// In en, this message translates to:
  /// **'Command plan'**
  String get commandPlanTitle;

  /// No description provided for @commandPreviewNotice.
  ///
  /// In en, this message translates to:
  /// **'Version operations always require a preview and your confirmation before a new version is created.'**
  String get commandPreviewNotice;

  /// No description provided for @speechLanguageLabel.
  ///
  /// In en, this message translates to:
  /// **'Recognition language'**
  String get speechLanguageLabel;

  /// No description provided for @speechLanguageHelp.
  ///
  /// In en, this message translates to:
  /// **'Choose Chinese or English for short commands; use automatic for mixed speech.'**
  String get speechLanguageHelp;

  /// No description provided for @speechLanguageTraditionalChinese.
  ///
  /// In en, this message translates to:
  /// **'Traditional Chinese'**
  String get speechLanguageTraditionalChinese;

  /// No description provided for @speechLanguageEnglish.
  ///
  /// In en, this message translates to:
  /// **'English'**
  String get speechLanguageEnglish;

  /// No description provided for @speechLanguageAutomatic.
  ///
  /// In en, this message translates to:
  /// **'Automatic detection'**
  String get speechLanguageAutomatic;

  /// No description provided for @speechResultMetadata.
  ///
  /// In en, this message translates to:
  /// **'Recognized as {language} · {model}'**
  String speechResultMetadata(String language, String model);

  /// No description provided for @speechStart.
  ///
  /// In en, this message translates to:
  /// **'Use microphone'**
  String get speechStart;

  /// No description provided for @speechStop.
  ///
  /// In en, this message translates to:
  /// **'Stop'**
  String get speechStop;

  /// No description provided for @speechCancel.
  ///
  /// In en, this message translates to:
  /// **'Cancel'**
  String get speechCancel;

  /// No description provided for @speechRequestingPermission.
  ///
  /// In en, this message translates to:
  /// **'Requesting microphone permission…'**
  String get speechRequestingPermission;

  /// No description provided for @speechRecordingSeconds.
  ///
  /// In en, this message translates to:
  /// **'Recording · {seconds}s'**
  String speechRecordingSeconds(int seconds);

  /// No description provided for @speechTranscribing.
  ///
  /// In en, this message translates to:
  /// **'Turning speech into editable text…'**
  String get speechTranscribing;

  /// No description provided for @speechPrivacyNotice.
  ///
  /// In en, this message translates to:
  /// **'Audio is processed by the local backend and is not saved to edit history.'**
  String get speechPrivacyNotice;

  /// No description provided for @speechUnavailable.
  ///
  /// In en, this message translates to:
  /// **'Microphone input is unavailable here. You can still type a prompt.'**
  String get speechUnavailable;

  /// No description provided for @statusSpeechCompleted.
  ///
  /// In en, this message translates to:
  /// **'Speech was added as editable text. Review it before applying.'**
  String get statusSpeechCompleted;

  /// No description provided for @statusSpeechCancelled.
  ///
  /// In en, this message translates to:
  /// **'Voice input was cancelled.'**
  String get statusSpeechCancelled;

  /// No description provided for @errorSpeechPermissionDenied.
  ///
  /// In en, this message translates to:
  /// **'Microphone permission was denied. Allow it in Chrome settings or type the prompt instead.'**
  String get errorSpeechPermissionDenied;

  /// No description provided for @errorSpeechNoMicrophone.
  ///
  /// In en, this message translates to:
  /// **'No usable microphone was found. Check the device or type the prompt instead.'**
  String get errorSpeechNoMicrophone;

  /// No description provided for @errorSpeechRecorderUnavailable.
  ///
  /// In en, this message translates to:
  /// **'This browser cannot provide the required microphone format. Use current Chrome or type the prompt instead.'**
  String get errorSpeechRecorderUnavailable;

  /// No description provided for @errorSpeechRecordingFailed.
  ///
  /// In en, this message translates to:
  /// **'Recording failed. Check the microphone and try again.'**
  String get errorSpeechRecordingFailed;

  /// No description provided for @errorSpeechNoAudio.
  ///
  /// In en, this message translates to:
  /// **'The microphone returned no usable audio. Please record again.'**
  String get errorSpeechNoAudio;

  /// No description provided for @errorSpeechInvalidAudio.
  ///
  /// In en, this message translates to:
  /// **'The recording could not be read. Please record again.'**
  String get errorSpeechInvalidAudio;

  /// No description provided for @errorSpeechUnsupportedFormat.
  ///
  /// In en, this message translates to:
  /// **'This recording format is not supported. Please record again in Chrome.'**
  String get errorSpeechUnsupportedFormat;

  /// No description provided for @errorSpeechNoSpeech.
  ///
  /// In en, this message translates to:
  /// **'No usable speech was detected. Move closer to the microphone and try again.'**
  String get errorSpeechNoSpeech;

  /// No description provided for @errorSpeechTooLong.
  ///
  /// In en, this message translates to:
  /// **'The recording is longer than 15 seconds. Please use a shorter editing prompt.'**
  String get errorSpeechTooLong;

  /// No description provided for @errorSpeechTooLarge.
  ///
  /// In en, this message translates to:
  /// **'The recording is too large. Please use a shorter editing prompt.'**
  String get errorSpeechTooLarge;

  /// No description provided for @errorSpeechModelUnavailable.
  ///
  /// In en, this message translates to:
  /// **'The local speech model is unavailable. Check the backend model and device settings.'**
  String get errorSpeechModelUnavailable;

  /// No description provided for @errorSpeechTranscriptionFailed.
  ///
  /// In en, this message translates to:
  /// **'Speech recognition failed. Please record again.'**
  String get errorSpeechTranscriptionFailed;

  /// No description provided for @errorSpeechTimeout.
  ///
  /// In en, this message translates to:
  /// **'Speech recognition took too long. Please try again.'**
  String get errorSpeechTimeout;

  /// No description provided for @errorSpeechBackendUnavailable.
  ///
  /// In en, this message translates to:
  /// **'Could not connect to the local speech backend. Typing is still available.'**
  String get errorSpeechBackendUnavailable;

  /// No description provided for @applyPrompt.
  ///
  /// In en, this message translates to:
  /// **'Apply prompt'**
  String get applyPrompt;

  /// No description provided for @styleCatalogTitle.
  ///
  /// In en, this message translates to:
  /// **'Style catalog'**
  String get styleCatalogTitle;

  /// No description provided for @styleCatalogUnavailable.
  ///
  /// In en, this message translates to:
  /// **'The style catalog is unavailable. Check that the backend is running.'**
  String get styleCatalogUnavailable;

  /// No description provided for @styleCatalogSubtitle.
  ///
  /// In en, this message translates to:
  /// **'{count} approved styles · v{version}'**
  String styleCatalogSubtitle(int count, String version);

  /// No description provided for @styleCategoryPrevious.
  ///
  /// In en, this message translates to:
  /// **'See previous style categories'**
  String get styleCategoryPrevious;

  /// No description provided for @styleCategoryNext.
  ///
  /// In en, this message translates to:
  /// **'See more style categories'**
  String get styleCategoryNext;

  /// No description provided for @styleCategoryAll.
  ///
  /// In en, this message translates to:
  /// **'All'**
  String get styleCategoryAll;

  /// No description provided for @styleStrength.
  ///
  /// In en, this message translates to:
  /// **'Strength'**
  String get styleStrength;

  /// No description provided for @applyStyle.
  ///
  /// In en, this message translates to:
  /// **'Apply style'**
  String get applyStyle;

  /// No description provided for @referenceEditTitle.
  ///
  /// In en, this message translates to:
  /// **'Reference edit'**
  String get referenceEditTitle;

  /// No description provided for @referenceFromOriginal.
  ///
  /// In en, this message translates to:
  /// **'Adjust the original toward the reference image colors'**
  String get referenceFromOriginal;

  /// No description provided for @referenceFromCurrent.
  ///
  /// In en, this message translates to:
  /// **'Apply the reference direction to the current version'**
  String get referenceFromCurrent;

  /// No description provided for @selectReference.
  ///
  /// In en, this message translates to:
  /// **'Choose reference'**
  String get selectReference;

  /// No description provided for @changeReference.
  ///
  /// In en, this message translates to:
  /// **'Change reference'**
  String get changeReference;

  /// No description provided for @removeReference.
  ///
  /// In en, this message translates to:
  /// **'Remove reference'**
  String get removeReference;

  /// No description provided for @applyReference.
  ///
  /// In en, this message translates to:
  /// **'Apply reference'**
  String get applyReference;

  /// No description provided for @manualEditTitle.
  ///
  /// In en, this message translates to:
  /// **'Manual adjustments'**
  String get manualEditTitle;

  /// No description provided for @manualSourceVersion.
  ///
  /// In en, this message translates to:
  /// **'Source · {target} · {mode}'**
  String manualSourceVersion(String target, String mode);

  /// No description provided for @advancedAdjustments.
  ///
  /// In en, this message translates to:
  /// **'Advanced adjustments'**
  String get advancedAdjustments;

  /// No description provided for @historyTitle.
  ///
  /// In en, this message translates to:
  /// **'History'**
  String get historyTitle;

  /// No description provided for @historyVersionCount.
  ///
  /// In en, this message translates to:
  /// **'{count} versions'**
  String historyVersionCount(int count);

  /// No description provided for @refreshHistory.
  ///
  /// In en, this message translates to:
  /// **'Sync history'**
  String get refreshHistory;

  /// No description provided for @selectedOriginalNewBranch.
  ///
  /// In en, this message translates to:
  /// **'Original selected · next edit creates a new branch'**
  String get selectedOriginalNewBranch;

  /// No description provided for @createBranchFromOriginal.
  ///
  /// In en, this message translates to:
  /// **'Create a new branch from the original'**
  String get createBranchFromOriginal;

  /// No description provided for @emptyHistory.
  ///
  /// In en, this message translates to:
  /// **'Versions appear here after your first edit.'**
  String get emptyHistory;

  /// No description provided for @currentPreview.
  ///
  /// In en, this message translates to:
  /// **'Current preview'**
  String get currentPreview;

  /// No description provided for @currentAdjustments.
  ///
  /// In en, this message translates to:
  /// **'Current adjustments'**
  String get currentAdjustments;

  /// No description provided for @styleEffectiveParameters.
  ///
  /// In en, this message translates to:
  /// **'Equivalent parameters at {strength}% strength. The style also uses curves, split toning, and other internal recipes.'**
  String styleEffectiveParameters(int strength);

  /// No description provided for @noManualParameters.
  ///
  /// In en, this message translates to:
  /// **'This version has no manual parameters to display.'**
  String get noManualParameters;

  /// No description provided for @styleUnderstanding.
  ///
  /// In en, this message translates to:
  /// **'Interpretation: applied {name} at {strength}% strength.'**
  String styleUnderstanding(String name, int strength);

  /// No description provided for @adjustmentCount.
  ///
  /// In en, this message translates to:
  /// **'{count} adjustments'**
  String adjustmentCount(int count);

  /// No description provided for @adaptiveIntervalReset.
  ///
  /// In en, this message translates to:
  /// **'Range reset'**
  String get adaptiveIntervalReset;

  /// No description provided for @adaptiveConverged.
  ///
  /// In en, this message translates to:
  /// **'Converged'**
  String get adaptiveConverged;

  /// No description provided for @adaptiveContinue.
  ///
  /// In en, this message translates to:
  /// **'Continuing fine-tune'**
  String get adaptiveContinue;

  /// No description provided for @adaptiveFineTune.
  ///
  /// In en, this message translates to:
  /// **'Adaptive fine-tune'**
  String get adaptiveFineTune;

  /// No description provided for @relativeAdjustment.
  ///
  /// In en, this message translates to:
  /// **'Relative change'**
  String get relativeAdjustment;

  /// No description provided for @candidateValue.
  ///
  /// In en, this message translates to:
  /// **'Candidate value'**
  String get candidateValue;

  /// No description provided for @currentBounds.
  ///
  /// In en, this message translates to:
  /// **'Current bounds'**
  String get currentBounds;

  /// No description provided for @stepSize.
  ///
  /// In en, this message translates to:
  /// **'Step'**
  String get stepSize;

  /// No description provided for @stepSizeWithTransform.
  ///
  /// In en, this message translates to:
  /// **'Step ({transform})'**
  String stepSizeWithTransform(String transform);

  /// No description provided for @adaptiveReasonInitial.
  ///
  /// In en, this message translates to:
  /// **'Create the initial step'**
  String get adaptiveReasonInitial;

  /// No description provided for @adaptiveReasonReverse.
  ///
  /// In en, this message translates to:
  /// **'Move back from the current effect'**
  String get adaptiveReasonReverse;

  /// No description provided for @adaptiveReasonHandoff.
  ///
  /// In en, this message translates to:
  /// **'Continue with a related parameter'**
  String get adaptiveReasonHandoff;

  /// No description provided for @adaptiveReasonMidpoint.
  ///
  /// In en, this message translates to:
  /// **'Use the interval midpoint based on feedback'**
  String get adaptiveReasonMidpoint;

  /// No description provided for @adaptiveReasonContinue.
  ///
  /// In en, this message translates to:
  /// **'Continue exploring in the same direction'**
  String get adaptiveReasonContinue;

  /// No description provided for @adaptiveReasonNarrow.
  ///
  /// In en, this message translates to:
  /// **'Narrow the range after opposite feedback'**
  String get adaptiveReasonNarrow;

  /// No description provided for @adaptiveReasonReanchor.
  ///
  /// In en, this message translates to:
  /// **'Rebuild the adjustment baseline'**
  String get adaptiveReasonReanchor;

  /// No description provided for @adaptiveReasonAbsolute.
  ///
  /// In en, this message translates to:
  /// **'Use an explicit value and reset the range'**
  String get adaptiveReasonAbsolute;

  /// No description provided for @adaptiveReasonRelative.
  ///
  /// In en, this message translates to:
  /// **'Apply a relative numeric change'**
  String get adaptiveReasonRelative;

  /// No description provided for @adaptiveReasonResetAxis.
  ///
  /// In en, this message translates to:
  /// **'Reset one parameter'**
  String get adaptiveReasonResetAxis;

  /// No description provided for @adaptiveReasonResetOriginal.
  ///
  /// In en, this message translates to:
  /// **'Return to the original'**
  String get adaptiveReasonResetOriginal;

  /// No description provided for @collapse.
  ///
  /// In en, this message translates to:
  /// **'Collapse'**
  String get collapse;

  /// No description provided for @resetParameter.
  ///
  /// In en, this message translates to:
  /// **'Reset {label} to neutral'**
  String resetParameter(String label);

  /// No description provided for @equivalentParameters.
  ///
  /// In en, this message translates to:
  /// **'Equivalent {summary}'**
  String equivalentParameters(String summary);

  /// No description provided for @historyVersionMode.
  ///
  /// In en, this message translates to:
  /// **'Version {version} · {mode}'**
  String historyVersionMode(int version, String mode);

  /// No description provided for @rootBranch.
  ///
  /// In en, this message translates to:
  /// **'Root branch'**
  String get rootBranch;

  /// No description provided for @continuesParent.
  ///
  /// In en, this message translates to:
  /// **'Continues parent version'**
  String get continuesParent;

  /// No description provided for @continuesVersion.
  ///
  /// In en, this message translates to:
  /// **'Continues version {version}'**
  String continuesVersion(int version);

  /// No description provided for @referenceNotSelected.
  ///
  /// In en, this message translates to:
  /// **'No reference selected'**
  String get referenceNotSelected;

  /// No description provided for @actionReset.
  ///
  /// In en, this message translates to:
  /// **'Reset'**
  String get actionReset;

  /// No description provided for @actionApply.
  ///
  /// In en, this message translates to:
  /// **'Apply'**
  String get actionApply;

  /// No description provided for @actionApplying.
  ///
  /// In en, this message translates to:
  /// **'Applying…'**
  String get actionApplying;

  /// No description provided for @notApplied.
  ///
  /// In en, this message translates to:
  /// **'Not applied'**
  String get notApplied;

  /// No description provided for @parameterExposure.
  ///
  /// In en, this message translates to:
  /// **'Exposure'**
  String get parameterExposure;

  /// No description provided for @parameterBrightness.
  ///
  /// In en, this message translates to:
  /// **'Brightness'**
  String get parameterBrightness;

  /// No description provided for @parameterContrast.
  ///
  /// In en, this message translates to:
  /// **'Contrast'**
  String get parameterContrast;

  /// No description provided for @parameterHighlights.
  ///
  /// In en, this message translates to:
  /// **'Highlights'**
  String get parameterHighlights;

  /// No description provided for @parameterShadows.
  ///
  /// In en, this message translates to:
  /// **'Shadows'**
  String get parameterShadows;

  /// No description provided for @parameterWhites.
  ///
  /// In en, this message translates to:
  /// **'Whites'**
  String get parameterWhites;

  /// No description provided for @parameterBlacks.
  ///
  /// In en, this message translates to:
  /// **'Blacks'**
  String get parameterBlacks;

  /// No description provided for @parameterSaturation.
  ///
  /// In en, this message translates to:
  /// **'Saturation'**
  String get parameterSaturation;

  /// No description provided for @parameterVibrance.
  ///
  /// In en, this message translates to:
  /// **'Vibrance'**
  String get parameterVibrance;

  /// No description provided for @parameterTemperature.
  ///
  /// In en, this message translates to:
  /// **'Temperature'**
  String get parameterTemperature;

  /// No description provided for @parameterWhiteBalanceTint.
  ///
  /// In en, this message translates to:
  /// **'White balance tint'**
  String get parameterWhiteBalanceTint;

  /// No description provided for @parameterSharpen.
  ///
  /// In en, this message translates to:
  /// **'Sharpen'**
  String get parameterSharpen;

  /// No description provided for @parameterClarity.
  ///
  /// In en, this message translates to:
  /// **'Clarity'**
  String get parameterClarity;

  /// No description provided for @parameterDehaze.
  ///
  /// In en, this message translates to:
  /// **'Dehaze'**
  String get parameterDehaze;

  /// No description provided for @parameterVignette.
  ///
  /// In en, this message translates to:
  /// **'Vignette'**
  String get parameterVignette;

  /// No description provided for @regionAll.
  ///
  /// In en, this message translates to:
  /// **'Whole image'**
  String get regionAll;

  /// No description provided for @regionSky.
  ///
  /// In en, this message translates to:
  /// **'Sky'**
  String get regionSky;

  /// No description provided for @regionPerson.
  ///
  /// In en, this message translates to:
  /// **'Person'**
  String get regionPerson;

  /// No description provided for @regionBackground.
  ///
  /// In en, this message translates to:
  /// **'Background'**
  String get regionBackground;

  /// No description provided for @regionHighlights.
  ///
  /// In en, this message translates to:
  /// **'Bright areas'**
  String get regionHighlights;

  /// No description provided for @regionShadows.
  ///
  /// In en, this message translates to:
  /// **'Dark areas'**
  String get regionShadows;

  /// No description provided for @regionCenter.
  ///
  /// In en, this message translates to:
  /// **'Center'**
  String get regionCenter;

  /// No description provided for @regionEdges.
  ///
  /// In en, this message translates to:
  /// **'Edges'**
  String get regionEdges;

  /// No description provided for @modePrompt.
  ///
  /// In en, this message translates to:
  /// **'Prompt'**
  String get modePrompt;

  /// No description provided for @modeStyle.
  ///
  /// In en, this message translates to:
  /// **'Style'**
  String get modeStyle;

  /// No description provided for @modeReference.
  ///
  /// In en, this message translates to:
  /// **'Reference'**
  String get modeReference;

  /// No description provided for @modeManual.
  ///
  /// In en, this message translates to:
  /// **'Manual'**
  String get modeManual;

  /// No description provided for @promptEditFallbackTitle.
  ///
  /// In en, this message translates to:
  /// **'Prompt edit'**
  String get promptEditFallbackTitle;

  /// No description provided for @referenceEditDisplayTitle.
  ///
  /// In en, this message translates to:
  /// **'Reference edit'**
  String get referenceEditDisplayTitle;

  /// No description provided for @manualEditDisplayTitle.
  ///
  /// In en, this message translates to:
  /// **'Manual adjustments'**
  String get manualEditDisplayTitle;

  /// No description provided for @parserLlm.
  ///
  /// In en, this message translates to:
  /// **'LLM interpretation'**
  String get parserLlm;

  /// No description provided for @parserRules.
  ///
  /// In en, this message translates to:
  /// **'Rule-based interpretation'**
  String get parserRules;

  /// No description provided for @parserReference.
  ///
  /// In en, this message translates to:
  /// **'Reference mode'**
  String get parserReference;

  /// No description provided for @parserManual.
  ///
  /// In en, this message translates to:
  /// **'Manual parameters'**
  String get parserManual;

  /// No description provided for @styleFamilyNaturalClean.
  ///
  /// In en, this message translates to:
  /// **'Natural & clean'**
  String get styleFamilyNaturalClean;

  /// No description provided for @styleFamilyPortraitSkin.
  ///
  /// In en, this message translates to:
  /// **'Portrait skin'**
  String get styleFamilyPortraitSkin;

  /// No description provided for @styleFamilyLandscapeTravel.
  ///
  /// In en, this message translates to:
  /// **'Landscape & travel'**
  String get styleFamilyLandscapeTravel;

  /// No description provided for @styleFamilyCinematic.
  ///
  /// In en, this message translates to:
  /// **'Cinematic'**
  String get styleFamilyCinematic;

  /// No description provided for @styleFamilyFilmRetro.
  ///
  /// In en, this message translates to:
  /// **'Film & retro'**
  String get styleFamilyFilmRetro;

  /// No description provided for @styleFamilyBlackWhite.
  ///
  /// In en, this message translates to:
  /// **'Black & white'**
  String get styleFamilyBlackWhite;

  /// No description provided for @styleFamilyNightNeon.
  ///
  /// In en, this message translates to:
  /// **'Night & neon'**
  String get styleFamilyNightNeon;

  /// No description provided for @styleFamilyPastelCreative.
  ///
  /// In en, this message translates to:
  /// **'Pastel & creative'**
  String get styleFamilyPastelCreative;

  /// No description provided for @summaryOriginalNewBranch.
  ///
  /// In en, this message translates to:
  /// **'Original · next edit creates a new branch'**
  String get summaryOriginalNewBranch;

  /// No description provided for @summaryChoosePhoto.
  ///
  /// In en, this message translates to:
  /// **'Choose a photo, then start with a prompt or reference image'**
  String get summaryChoosePhoto;

  /// No description provided for @summaryPreviewPrefix.
  ///
  /// In en, this message translates to:
  /// **'Preview · '**
  String get summaryPreviewPrefix;

  /// No description provided for @manualUnavailableNeedPrompt.
  ///
  /// In en, this message translates to:
  /// **'Complete a prompt edit before opening manual adjustments.'**
  String get manualUnavailableNeedPrompt;

  /// No description provided for @manualUnavailableReference.
  ///
  /// In en, this message translates to:
  /// **'Reference results cannot be manually adjusted yet. Select a prompt or manual version first.'**
  String get manualUnavailableReference;

  /// No description provided for @manualUnavailableEngine.
  ///
  /// In en, this message translates to:
  /// **'The first manual adjustment version only supports OpenCV results.'**
  String get manualUnavailableEngine;

  /// No description provided for @manualUnavailableGeneric.
  ///
  /// In en, this message translates to:
  /// **'This version does not support manual adjustments.'**
  String get manualUnavailableGeneric;

  /// No description provided for @statusSelectedNewOriginal.
  ///
  /// In en, this message translates to:
  /// **'New original photo selected'**
  String get statusSelectedNewOriginal;

  /// No description provided for @statusReferenceReady.
  ///
  /// In en, this message translates to:
  /// **'Reference image ready'**
  String get statusReferenceReady;

  /// No description provided for @errorPromptRequired.
  ///
  /// In en, this message translates to:
  /// **'Enter an editing prompt.'**
  String get errorPromptRequired;

  /// No description provided for @errorReferenceRequired.
  ///
  /// In en, this message translates to:
  /// **'Choose a reference image first.'**
  String get errorReferenceRequired;

  /// No description provided for @errorStyleCatalogLoad.
  ///
  /// In en, this message translates to:
  /// **'Could not load style catalog: {error}'**
  String errorStyleCatalogLoad(String error);

  /// No description provided for @errorOriginalRequired.
  ///
  /// In en, this message translates to:
  /// **'Choose an original photo first.'**
  String get errorOriginalRequired;

  /// No description provided for @statusParsingPrompt.
  ///
  /// In en, this message translates to:
  /// **'Interpreting edit prompt…'**
  String get statusParsingPrompt;

  /// No description provided for @statusApplyingReference.
  ///
  /// In en, this message translates to:
  /// **'Applying reference image…'**
  String get statusApplyingReference;

  /// No description provided for @statusEditComplete.
  ///
  /// In en, this message translates to:
  /// **'Edit complete'**
  String get statusEditComplete;

  /// No description provided for @errorEditFailed.
  ///
  /// In en, this message translates to:
  /// **'Edit failed: {error}'**
  String errorEditFailed(String error);

  /// No description provided for @statusHistorySynced.
  ///
  /// In en, this message translates to:
  /// **'History synced'**
  String get statusHistorySynced;

  /// No description provided for @statusSwitchedHistory.
  ///
  /// In en, this message translates to:
  /// **'Switched to history version'**
  String get statusSwitchedHistory;

  /// No description provided for @statusSwitchedOriginal.
  ///
  /// In en, this message translates to:
  /// **'Switched to original. A new history branch can now be created.'**
  String get statusSwitchedOriginal;

  /// No description provided for @errorOpenManual.
  ///
  /// In en, this message translates to:
  /// **'Could not open manual adjustments: {error}'**
  String errorOpenManual(String error);

  /// No description provided for @statusResetSourceParameters.
  ///
  /// In en, this message translates to:
  /// **'Restored source version parameters'**
  String get statusResetSourceParameters;

  /// No description provided for @errorManualPreview.
  ///
  /// In en, this message translates to:
  /// **'Manual preview failed: {error}'**
  String errorManualPreview(String error);

  /// No description provided for @statusApplyingManual.
  ///
  /// In en, this message translates to:
  /// **'Applying manual adjustments…'**
  String get statusApplyingManual;

  /// No description provided for @statusManualCommitted.
  ///
  /// In en, this message translates to:
  /// **'Manual adjustments applied and added to history'**
  String get statusManualCommitted;

  /// No description provided for @errorManualCommit.
  ///
  /// In en, this message translates to:
  /// **'Could not apply manual adjustments: {error}'**
  String errorManualCommit(String error);

  /// No description provided for @errorStyleAmbiguous.
  ///
  /// In en, this message translates to:
  /// **'This description matches multiple styles. Choose a specific style from the catalog.'**
  String get errorStyleAmbiguous;

  /// No description provided for @errorStyleCompound.
  ///
  /// In en, this message translates to:
  /// **'Apply a style first, then adjust brightness, color, or other parameters in a follow-up prompt.'**
  String get errorStyleCompound;

  /// No description provided for @errorStyleAsset.
  ///
  /// In en, this message translates to:
  /// **'Style asset or version validation failed. No substitute style was applied.'**
  String get errorStyleAsset;

  /// No description provided for @errorSemanticTargetNotFound.
  ///
  /// In en, this message translates to:
  /// **'The requested area was not found in this photo. Try another photo or edit the whole image.'**
  String get errorSemanticTargetNotFound;

  /// No description provided for @errorAdaptiveClarification.
  ///
  /// In en, this message translates to:
  /// **'I am not sure which setting to fine-tune. Name a parameter or region.'**
  String get errorAdaptiveClarification;

  /// No description provided for @errorAdaptiveConverged.
  ///
  /// In en, this message translates to:
  /// **'This adjustment is near the minimum step. Use manual parameters for a final fine-tune.'**
  String get errorAdaptiveConverged;

  /// No description provided for @errorAdaptiveSatisfied.
  ///
  /// In en, this message translates to:
  /// **'The current result was kept. No duplicate history version was added.'**
  String get errorAdaptiveSatisfied;

  /// No description provided for @errorManualSourceUnsupported.
  ///
  /// In en, this message translates to:
  /// **'Reference results cannot be manually adjusted yet. Select a prompt or manual version.'**
  String get errorManualSourceUnsupported;

  /// No description provided for @errorBackendUnavailable.
  ///
  /// In en, this message translates to:
  /// **'Could not connect to the editing backend. Check that it is running.'**
  String get errorBackendUnavailable;

  /// No description provided for @errorCheckPrompt.
  ///
  /// In en, this message translates to:
  /// **'Review the editing prompt and try again.'**
  String get errorCheckPrompt;

  /// No description provided for @adaptiveIssuesContext.
  ///
  /// In en, this message translates to:
  /// **'{message} (involves: {contexts})'**
  String adaptiveIssuesContext(String message, String contexts);

  /// No description provided for @networkBackendError.
  ///
  /// In en, this message translates to:
  /// **'Could not connect to the editing backend: {error}'**
  String networkBackendError(String error);

  /// No description provided for @backendHttpError.
  ///
  /// In en, this message translates to:
  /// **'Backend request failed (HTTP {statusCode})'**
  String backendHttpError(int statusCode);

  /// No description provided for @backendInvalidResponse.
  ///
  /// In en, this message translates to:
  /// **'The backend returned an unrecognized data format.'**
  String get backendInvalidResponse;

  /// No description provided for @photoGitTitle.
  ///
  /// In en, this message translates to:
  /// **'Version operations'**
  String get photoGitTitle;

  /// No description provided for @photoGitSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Merge or selectively undo tracked edits'**
  String get photoGitSubtitle;

  /// No description provided for @photoGitUnavailable.
  ///
  /// In en, this message translates to:
  /// **'Select an OpenCV history version to use version operations.'**
  String get photoGitUnavailable;

  /// No description provided for @photoGitManualDraftBlocked.
  ///
  /// In en, this message translates to:
  /// **'Finish or discard the manual adjustment draft before starting a version operation.'**
  String get photoGitManualDraftBlocked;

  /// No description provided for @photoGitMerge.
  ///
  /// In en, this message translates to:
  /// **'Merge versions'**
  String get photoGitMerge;

  /// No description provided for @photoGitSelectiveRevert.
  ///
  /// In en, this message translates to:
  /// **'Selective undo'**
  String get photoGitSelectiveRevert;

  /// No description provided for @photoGitDeterministic.
  ///
  /// In en, this message translates to:
  /// **'Deterministic version plan'**
  String get photoGitDeterministic;

  /// No description provided for @photoGitTarget.
  ///
  /// In en, this message translates to:
  /// **'Target'**
  String get photoGitTarget;

  /// No description provided for @photoGitSource.
  ///
  /// In en, this message translates to:
  /// **'Source'**
  String get photoGitSource;

  /// No description provided for @photoGitRevertStep.
  ///
  /// In en, this message translates to:
  /// **'Step to undo'**
  String get photoGitRevertStep;

  /// No description provided for @photoGitChooseSource.
  ///
  /// In en, this message translates to:
  /// **'Choose another version'**
  String get photoGitChooseSource;

  /// No description provided for @photoGitChooseRevertStep.
  ///
  /// In en, this message translates to:
  /// **'Choose a step from the target lineage'**
  String get photoGitChooseRevertStep;

  /// No description provided for @photoGitInstruction.
  ///
  /// In en, this message translates to:
  /// **'Scope description'**
  String get photoGitInstruction;

  /// No description provided for @photoGitMergeHint.
  ///
  /// In en, this message translates to:
  /// **'For example: bring in only the sky saturation'**
  String get photoGitMergeHint;

  /// No description provided for @photoGitRevertHint.
  ///
  /// In en, this message translates to:
  /// **'For example: undo only this step\'s saturation'**
  String get photoGitRevertHint;

  /// No description provided for @photoGitScopeAssist.
  ///
  /// In en, this message translates to:
  /// **'Optional scope shortcuts'**
  String get photoGitScopeAssist;

  /// No description provided for @photoGitAnyRegion.
  ///
  /// In en, this message translates to:
  /// **'Any region'**
  String get photoGitAnyRegion;

  /// No description provided for @photoGitAnyParameter.
  ///
  /// In en, this message translates to:
  /// **'Any parameter'**
  String get photoGitAnyParameter;

  /// No description provided for @photoGitAnalyze.
  ///
  /// In en, this message translates to:
  /// **'Analyze changes'**
  String get photoGitAnalyze;

  /// No description provided for @photoGitAnalyzing.
  ///
  /// In en, this message translates to:
  /// **'Analyzing…'**
  String get photoGitAnalyzing;

  /// No description provided for @photoGitPlanSummary.
  ///
  /// In en, this message translates to:
  /// **'Plan summary'**
  String get photoGitPlanSummary;

  /// No description provided for @photoGitAdded.
  ///
  /// In en, this message translates to:
  /// **'Added'**
  String get photoGitAdded;

  /// No description provided for @photoGitRemoved.
  ///
  /// In en, this message translates to:
  /// **'Removed'**
  String get photoGitRemoved;

  /// No description provided for @photoGitConflicts.
  ///
  /// In en, this message translates to:
  /// **'Conflicts'**
  String get photoGitConflicts;

  /// No description provided for @photoGitNoContribution.
  ///
  /// In en, this message translates to:
  /// **'No matching tracked change.'**
  String get photoGitNoContribution;

  /// No description provided for @photoGitConflictHelp.
  ///
  /// In en, this message translates to:
  /// **'Resolve every conflict before previewing.'**
  String get photoGitConflictHelp;

  /// No description provided for @photoGitKeepTarget.
  ///
  /// In en, this message translates to:
  /// **'Keep target'**
  String get photoGitKeepTarget;

  /// No description provided for @photoGitUseSource.
  ///
  /// In en, this message translates to:
  /// **'Use source'**
  String get photoGitUseSource;

  /// No description provided for @photoGitReplayLater.
  ///
  /// In en, this message translates to:
  /// **'Undo and replay later edits'**
  String get photoGitReplayLater;

  /// No description provided for @photoGitPreview.
  ///
  /// In en, this message translates to:
  /// **'Create preview'**
  String get photoGitPreview;

  /// No description provided for @photoGitPreviewing.
  ///
  /// In en, this message translates to:
  /// **'Rendering preview…'**
  String get photoGitPreviewing;

  /// No description provided for @photoGitCommit.
  ///
  /// In en, this message translates to:
  /// **'Create version'**
  String get photoGitCommit;

  /// No description provided for @photoGitCommitting.
  ///
  /// In en, this message translates to:
  /// **'Creating version…'**
  String get photoGitCommitting;

  /// No description provided for @photoGitCancel.
  ///
  /// In en, this message translates to:
  /// **'Cancel operation'**
  String get photoGitCancel;

  /// No description provided for @photoGitMergedFrom.
  ///
  /// In en, this message translates to:
  /// **'Merged from'**
  String get photoGitMergedFrom;

  /// No description provided for @photoGitRevertedFrom.
  ///
  /// In en, this message translates to:
  /// **'Undid effects from'**
  String get photoGitRevertedFrom;

  /// No description provided for @photoGitCommonAncestor.
  ///
  /// In en, this message translates to:
  /// **'Common ancestor'**
  String get photoGitCommonAncestor;

  /// No description provided for @photoGitSchema.
  ///
  /// In en, this message translates to:
  /// **'Recipe version'**
  String get photoGitSchema;

  /// No description provided for @photoGitPlanHash.
  ///
  /// In en, this message translates to:
  /// **'Plan'**
  String get photoGitPlanHash;

  /// No description provided for @photoGitResolutions.
  ///
  /// In en, this message translates to:
  /// **'Conflict decisions'**
  String get photoGitResolutions;

  /// No description provided for @photoGitTargetValue.
  ///
  /// In en, this message translates to:
  /// **'Target value'**
  String get photoGitTargetValue;

  /// No description provided for @photoGitSourceValue.
  ///
  /// In en, this message translates to:
  /// **'Source value'**
  String get photoGitSourceValue;

  /// No description provided for @photoGitLaterChanges.
  ///
  /// In en, this message translates to:
  /// **'Later edits'**
  String get photoGitLaterChanges;

  /// No description provided for @statusPhotoGitPlanning.
  ///
  /// In en, this message translates to:
  /// **'Analyzing version differences…'**
  String get statusPhotoGitPlanning;

  /// No description provided for @statusPhotoGitConflictsFound.
  ///
  /// In en, this message translates to:
  /// **'Conflicts found. Choose a result for each one.'**
  String get statusPhotoGitConflictsFound;

  /// No description provided for @statusPhotoGitNoChange.
  ///
  /// In en, this message translates to:
  /// **'The selected scope would not change the target.'**
  String get statusPhotoGitNoChange;

  /// No description provided for @statusPhotoGitPlanReady.
  ///
  /// In en, this message translates to:
  /// **'The version plan is ready.'**
  String get statusPhotoGitPlanReady;

  /// No description provided for @statusPhotoGitPreviewing.
  ///
  /// In en, this message translates to:
  /// **'Rendering the version preview…'**
  String get statusPhotoGitPreviewing;

  /// No description provided for @statusPhotoGitPreviewReady.
  ///
  /// In en, this message translates to:
  /// **'Preview ready. Compare it before creating the version.'**
  String get statusPhotoGitPreviewReady;

  /// No description provided for @statusPhotoGitCommitting.
  ///
  /// In en, this message translates to:
  /// **'Creating a tracked version…'**
  String get statusPhotoGitCommitting;

  /// No description provided for @statusPhotoGitCommitted.
  ///
  /// In en, this message translates to:
  /// **'The version was added to history.'**
  String get statusPhotoGitCommitted;

  /// No description provided for @errorPhotoGitRequestIncomplete.
  ///
  /// In en, this message translates to:
  /// **'Choose a version and specify a region or parameter.'**
  String get errorPhotoGitRequestIncomplete;

  /// No description provided for @errorPhotoGitPlan.
  ///
  /// In en, this message translates to:
  /// **'Version analysis failed: {error}'**
  String errorPhotoGitPlan(String error);

  /// No description provided for @errorPhotoGitPreview.
  ///
  /// In en, this message translates to:
  /// **'Version preview failed: {error}'**
  String errorPhotoGitPreview(String error);

  /// No description provided for @errorPhotoGitCommit.
  ///
  /// In en, this message translates to:
  /// **'Could not create the version: {error}'**
  String errorPhotoGitCommit(String error);

  /// No description provided for @errorPhotoGitScope.
  ///
  /// In en, this message translates to:
  /// **'Specify a supported region or parameter in text or with a shortcut.'**
  String get errorPhotoGitScope;

  /// No description provided for @errorPhotoGitConflict.
  ///
  /// In en, this message translates to:
  /// **'Some conflicts are unresolved. Choose each result and try again.'**
  String get errorPhotoGitConflict;

  /// No description provided for @errorPhotoGitStale.
  ///
  /// In en, this message translates to:
  /// **'The version changed. Analyze it again before previewing.'**
  String get errorPhotoGitStale;

  /// No description provided for @errorPhotoGitNoChange.
  ///
  /// In en, this message translates to:
  /// **'The selected content matches the target, so no duplicate version was created.'**
  String get errorPhotoGitNoChange;

  /// No description provided for @errorPhotoGitUnsupported.
  ///
  /// In en, this message translates to:
  /// **'This version does not contain enough tracked information for a safe operation.'**
  String get errorPhotoGitUnsupported;

  /// No description provided for @errorPhotoGitDraftActive.
  ///
  /// In en, this message translates to:
  /// **'Finish or cancel the current version operation first.'**
  String get errorPhotoGitDraftActive;

  /// No description provided for @contractBadgePassed.
  ///
  /// In en, this message translates to:
  /// **'Contract {passed}/{total} passed'**
  String contractBadgePassed(int passed, int total);

  /// No description provided for @contractBadgeAdjusted.
  ///
  /// In en, this message translates to:
  /// **'Adjusted to {scale}% to meet the contract'**
  String contractBadgeAdjusted(int scale);

  /// No description provided for @contractDetailsTitle.
  ///
  /// In en, this message translates to:
  /// **'Verified edit contract'**
  String get contractDetailsTitle;

  /// No description provided for @contractStatusPassed.
  ///
  /// In en, this message translates to:
  /// **'Passed at requested strength'**
  String get contractStatusPassed;

  /// No description provided for @contractStatusAdjusted.
  ///
  /// In en, this message translates to:
  /// **'Passed after a safe strength adjustment'**
  String get contractStatusAdjusted;

  /// No description provided for @contractChecks.
  ///
  /// In en, this message translates to:
  /// **'Verification checks'**
  String get contractChecks;

  /// No description provided for @contractConstraints.
  ///
  /// In en, this message translates to:
  /// **'Understood constraints'**
  String get contractConstraints;

  /// No description provided for @contractRequestedScale.
  ///
  /// In en, this message translates to:
  /// **'Requested strength'**
  String get contractRequestedScale;

  /// No description provided for @contractAppliedScale.
  ///
  /// In en, this message translates to:
  /// **'Applied strength'**
  String get contractAppliedScale;

  /// No description provided for @contractThreshold.
  ///
  /// In en, this message translates to:
  /// **'Threshold'**
  String get contractThreshold;

  /// No description provided for @contractThresholdSource.
  ///
  /// In en, this message translates to:
  /// **'Threshold source'**
  String get contractThresholdSource;

  /// No description provided for @contractBaseline.
  ///
  /// In en, this message translates to:
  /// **'Baseline'**
  String get contractBaseline;

  /// No description provided for @contractActual.
  ///
  /// In en, this message translates to:
  /// **'Actual'**
  String get contractActual;

  /// No description provided for @contractMetricVersion.
  ///
  /// In en, this message translates to:
  /// **'Metric version'**
  String get contractMetricVersion;

  /// No description provided for @contractTargetVersion.
  ///
  /// In en, this message translates to:
  /// **'Verified target'**
  String get contractTargetVersion;

  /// No description provided for @contractParentVersion.
  ///
  /// In en, this message translates to:
  /// **'Parent'**
  String get contractParentVersion;

  /// No description provided for @contractVerificationTime.
  ///
  /// In en, this message translates to:
  /// **'Verification time'**
  String get contractVerificationTime;

  /// No description provided for @contractVersions.
  ///
  /// In en, this message translates to:
  /// **'Contract versions'**
  String get contractVersions;

  /// No description provided for @contractRequestedParameters.
  ///
  /// In en, this message translates to:
  /// **'Requested parameters'**
  String get contractRequestedParameters;

  /// No description provided for @contractActualParameters.
  ///
  /// In en, this message translates to:
  /// **'Applied parameters'**
  String get contractActualParameters;

  /// No description provided for @contractPolicyDefault.
  ///
  /// In en, this message translates to:
  /// **'Versioned policy default'**
  String get contractPolicyDefault;

  /// No description provided for @contractExplicitUser.
  ///
  /// In en, this message translates to:
  /// **'Specified by user'**
  String get contractExplicitUser;

  /// No description provided for @contractSystemPolicy.
  ///
  /// In en, this message translates to:
  /// **'System safety policy'**
  String get contractSystemPolicy;

  /// No description provided for @contractOperatorAtMost.
  ///
  /// In en, this message translates to:
  /// **'At most'**
  String get contractOperatorAtMost;

  /// No description provided for @contractOperatorNoWorse.
  ///
  /// In en, this message translates to:
  /// **'No worse than baseline'**
  String get contractOperatorNoWorse;

  /// No description provided for @contractCheckPassed.
  ///
  /// In en, this message translates to:
  /// **'Passed'**
  String get contractCheckPassed;

  /// No description provided for @contractCheckFailed.
  ///
  /// In en, this message translates to:
  /// **'Failed'**
  String get contractCheckFailed;

  /// No description provided for @contractUnknownMetric.
  ///
  /// In en, this message translates to:
  /// **'Unknown metric'**
  String get contractUnknownMetric;

  /// No description provided for @contractNoChecks.
  ///
  /// In en, this message translates to:
  /// **'No verification check data was returned.'**
  String get contractNoChecks;

  /// No description provided for @contractMilliseconds.
  ///
  /// In en, this message translates to:
  /// **'{value} ms'**
  String contractMilliseconds(String value);

  /// No description provided for @errorContractClarification.
  ///
  /// In en, this message translates to:
  /// **'One or more protection conditions are unclear. Clarify the metric, area, or limit and try again.'**
  String get errorContractClarification;

  /// No description provided for @errorContractUnsupported.
  ///
  /// In en, this message translates to:
  /// **'This protection condition or required photo area cannot be verified yet. No edit was applied.'**
  String get errorContractUnsupported;

  /// No description provided for @errorContractUnsatisfied.
  ///
  /// In en, this message translates to:
  /// **'No effective edit could satisfy every protection condition. Adjust the limit and try again.'**
  String get errorContractUnsatisfied;

  /// No description provided for @errorContractNoChange.
  ///
  /// In en, this message translates to:
  /// **'The safe result would not create a visible change, so no duplicate version was added.'**
  String get errorContractNoChange;

  /// No description provided for @errorContractConflict.
  ///
  /// In en, this message translates to:
  /// **'This request ID was already used for different edit content. Submit the current prompt again.'**
  String get errorContractConflict;

  /// No description provided for @errorContractSchema.
  ///
  /// In en, this message translates to:
  /// **'Contract display metadata could not be loaded. Metric identifiers remain available.'**
  String get errorContractSchema;
}

class _AppLocalizationsDelegate
    extends LocalizationsDelegate<AppLocalizations> {
  const _AppLocalizationsDelegate();

  @override
  Future<AppLocalizations> load(Locale locale) {
    return SynchronousFuture<AppLocalizations>(lookupAppLocalizations(locale));
  }

  @override
  bool isSupported(Locale locale) =>
      <String>['en', 'zh'].contains(locale.languageCode);

  @override
  bool shouldReload(_AppLocalizationsDelegate old) => false;
}

AppLocalizations lookupAppLocalizations(Locale locale) {
  // Lookup logic when language+country codes are specified.
  switch (locale.languageCode) {
    case 'zh':
      {
        switch (locale.countryCode) {
          case 'TW':
            return AppLocalizationsZhTw();
        }
        break;
      }
  }

  // Lookup logic when only language code is specified.
  switch (locale.languageCode) {
    case 'en':
      return AppLocalizationsEn();
    case 'zh':
      return AppLocalizationsZh();
  }

  throw FlutterError(
    'AppLocalizations.delegate failed to load unsupported locale "$locale". This is likely '
    'an issue with the localizations generation tool. Please file an issue '
    'on GitHub with a reproducible sample app and the gen-l10n configuration '
    'that was used.',
  );
}
