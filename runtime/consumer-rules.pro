# Stratum runtime + generated adapters -- required because these classes
# are found only via JNI FindClass(String) at runtime, never via a direct
# Java bytecode reference R8's reachability analysis can see. Without
# this, a minified release build silently renames/strips them and every
# FindClass() call starts throwing "class not found on this device" for
# classes that are still physically present in the APK.
-keep class com.stratum.runtime.** { *; }
-keepclassmembers class com.stratum.runtime.** { *; }
-keep class com.stratum.adapters.** { *; }
-keepclassmembers class com.stratum.adapters.** { *; }
-keepclasseswithmembernames class * {
    native <methods>;
}