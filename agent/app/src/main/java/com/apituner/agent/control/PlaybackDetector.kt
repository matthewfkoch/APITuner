package com.apituner.agent.control

import android.content.ComponentName
import android.content.Context
import android.media.session.MediaSessionManager
import android.media.session.PlaybackState
import android.provider.Settings

/** Reads media playback state via active MediaSessions (needs Notification access). */
class PlaybackDetector(private val context: Context) {

    private val listenerComponent =
        ComponentName(context, PlaybackNotificationListener::class.java)

    fun hasPermission(): Boolean {
        return try {
            val enabled = Settings.Secure.getString(
                context.contentResolver, "enabled_notification_listeners"
            ) ?: return false
            enabled.split(":").any { it.contains(context.packageName) }
        } catch (e: Exception) {
            false
        }
    }

    /** Returns Pair(isPlaying, packageName-of-active-session) or (null, null) on failure. */
    fun playbackState(): Pair<Boolean?, String?> {
        return try {
            val msm =
                context.getSystemService(Context.MEDIA_SESSION_SERVICE) as MediaSessionManager
            val sessions = msm.getActiveSessions(listenerComponent)
            for (controller in sessions) {
                val state = controller.playbackState ?: continue
                if (state.state == PlaybackState.STATE_PLAYING ||
                    state.state == PlaybackState.STATE_BUFFERING
                ) {
                    return true to controller.packageName
                }
            }
            // A session exists but nothing is actively playing.
            val pkg = sessions.firstOrNull()?.packageName
            false to pkg
        } catch (e: SecurityException) {
            null to null
        } catch (e: Exception) {
            null to null
        }
    }
}
