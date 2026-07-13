package com.apituner.agent.control

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityEvent

/**
 * Accessibility service that exposes the only key actions a non-root app can
 * perform globally: BACK, HOME and RECENTS. Held as a singleton so the web
 * server can invoke actions when the user has enabled the service.
 */
class KeyAccessibilityService : AccessibilityService() {

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
    }

    override fun onDestroy() {
        if (instance === this) instance = null
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}

    override fun onInterrupt() {}

    fun sendKey(key: String): Boolean {
        val action = when (key.uppercase()) {
            "BACK" -> GLOBAL_ACTION_BACK
            "HOME" -> GLOBAL_ACTION_HOME
            "RECENTS", "APP_SWITCH" -> GLOBAL_ACTION_RECENTS
            else -> return false
        }
        return performGlobalAction(action)
    }

    companion object {
        @Volatile
        var instance: KeyAccessibilityService? = null

        fun isEnabled(): Boolean = instance != null
    }
}
