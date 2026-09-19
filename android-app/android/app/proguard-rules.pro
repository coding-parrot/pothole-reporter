# Add project specific ProGuard rules here.
# You can control the set of applied configuration files using the
# proguardFiles setting in build.gradle.
#
# For more details, see
#   http://developer.android.com/guide/developing/tools/proguard.html

# If your project uses WebView with JS, uncomment the following
# and specify the fully qualified class name to the JavaScript interface
# class:
#-keepclassmembers class fqcn.of.javascript.interface.for.webview {
#   public *;
#}

# Uncomment this to preserve the line number information for
# debugging stack traces.
#-keepattributes SourceFile,LineNumberTable

# If you keep the line number information, uncomment this to
# hide the original source file name.
#-renamesourcefileattribute SourceFile

# --- Pothole Reporter native Drive Mode ---

# Room: keep entity classes (Room uses reflection for column mapping)
-keep class com.gauravsen.potholereporter.db.entities.** { *; }
-keep class com.gauravsen.potholereporter.db.dao.** { *; }
-keep class com.gauravsen.potholereporter.db.AppDatabase { *; }

# Capacitor plugins: keep @PluginMethod methods accessible via reflection. Every class
# passed to registerPlugin() needs one of these, or R8 renames the bridge methods and
# the WebView's calls resolve to nothing in a release build.
-keep class com.gauravsen.potholereporter.bridge.DriveModePlugin { *; }
-keep class com.gauravsen.potholereporter.bridge.VideoImportPlugin { *; }

# OkHttp
-dontwarn okhttp3.**
-dontwarn okio.**
-keep class okhttp3.** { *; }
-keep interface okhttp3.** { *; }

# Kotlin coroutines
-keepnames class kotlinx.coroutines.internal.MainDispatcherFactory {}
-keepnames class kotlinx.coroutines.CoroutineExceptionHandler {}
-keepclassmembers class kotlinx.coroutines.** { volatile <fields>; }

# WorkManager
-keep class * extends androidx.work.Worker
-keep class * extends androidx.work.ListenableWorker {
    public <init>(android.content.Context, androidx.work.WorkerParameters);
}

# CameraX: keep internal classes that use reflection
-keep class androidx.camera.** { *; }

# Keep the notification action constants for PendingIntents
-keep class com.gauravsen.potholereporter.drivemode.DriveModeService {
    static final java.lang.String ACTION_*;
}
