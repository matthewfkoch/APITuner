package com.apituner.agent.util

import android.content.Context
import android.content.pm.PackageManager
import android.os.Build

data class AgentVersionInfo(
    val versionName: String,
    val versionCode: Long,
    val apkNameHint: String,
) {
    val isDebugBuild: Boolean
        get() = apkNameHint.contains("-debug", ignoreCase = true) ||
            versionName.contains("debug", ignoreCase = true)
}

object AgentVersion {
    fun current(context: Context): AgentVersionInfo {
        val pm = context.packageManager
        val pkg = context.packageName
        val info = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            pm.getPackageInfo(pkg, PackageManager.PackageInfoFlags.of(0))
        } else {
            @Suppress("DEPRECATION")
            pm.getPackageInfo(pkg, 0)
        }
        val code = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            info.longVersionCode
        } else {
            @Suppress("DEPRECATION")
            info.versionCode.toLong()
        }
        val name = info.versionName ?: "0"
        // Debug builds from this project use applicationIdSuffix / naming via
        // Gradle outputs; use debuggable flag as a stable local signal.
        val debuggable = (context.applicationInfo.flags and android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE) != 0
        val hint = if (debuggable) "debug" else "release"
        return AgentVersionInfo(versionName = name, versionCode = code, apkNameHint = hint)
    }
}
