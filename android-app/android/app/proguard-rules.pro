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

# Capacitor reads @CapacitorPlugin(permissions = {@Permission(...)}) and the
# @PermissionCallback / @ActivityCallback methods by reflection at runtime. capacitor-android
# is a project module here, so its consumer rules never reach this app, and R8 stripped
# the annotations. The first camera permission check then hit a NullPointerException in
# Plugin.getPermissionStates() on the CapacitorPlugins thread and the process died: the
# "Continue on the camera and location notice closes the app" report, release builds only.
-keepattributes *Annotation*,RuntimeVisibleAnnotations,RuntimeVisibleParameterAnnotations,Signature,InnerClasses,EnclosingMethod
-keep @interface com.getcapacitor.annotation.CapacitorPlugin { *; }
-keep @interface com.getcapacitor.annotation.Permission { *; }
-keep @interface com.getcapacitor.annotation.PermissionCallback { *; }
-keep @interface com.getcapacitor.annotation.ActivityCallback { *; }
-keep @interface com.getcapacitor.PluginMethod { *; }
-keep @com.getcapacitor.annotation.CapacitorPlugin class * extends com.getcapacitor.Plugin { *; }
-keepclassmembers class * extends com.getcapacitor.Plugin {
    @com.getcapacitor.PluginMethod <methods>;
    @com.getcapacitor.annotation.PermissionCallback <methods>;
    @com.getcapacitor.annotation.ActivityCallback <methods>;
}
-keep class com.getcapacitor.** { *; }
-keep class com.capacitorjs.plugins.** { *; }
-keep class name.ratson.cordova.** { *; }
-dontwarn com.getcapacitor.**

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
