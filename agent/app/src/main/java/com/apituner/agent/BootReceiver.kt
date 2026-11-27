package com.apituner.agent

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * Starts the Agent foreground service after reboot or APK update so the HTTP
 * API on port 9092 is available without opening the app manually.
 */
class BootReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent?) {
        val action = intent?.action ?: return
        if (!isBootAction(action) && action != Intent.ACTION_MY_PACKAGE_REPLACED) return

        val appContext = context.applicationContext
        val delayMs = if (action == Intent.ACTION_MY_PACKAGE_REPLACED) 0L else BOOT_DELAY_MS
        Log.i(TAG, "Starting AgentService (${delayMs}ms server bind delay) after $action")
        // Start the foreground service immediately rather than holding goAsync()
        // past Android's 10-second BroadcastReceiver ANR window.
        try {
            AgentService.start(appContext, startDelayMs = delayMs)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start AgentService after $action: ${e.message}", e)
        }
    }

    private fun isBootAction(action: String): Boolean =
        action == Intent.ACTION_BOOT_COMPLETED ||
            action == Intent.ACTION_LOCKED_BOOT_COMPLETED ||
            action == QUICKBOOT_POWERON

    companion object {
        private const val TAG = "BootReceiver"
        // Chromecast / Google TV often need a moment for networking after boot.
        private const val BOOT_DELAY_MS = 15_000L
        private const val QUICKBOOT_POWERON = "android.intent.action.QUICKBOOT_POWERON"
    }
}
