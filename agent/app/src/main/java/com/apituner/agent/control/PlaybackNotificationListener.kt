package com.apituner.agent.control

import android.service.notification.NotificationListenerService

/**
 * Enabling this listener (Settings > Notification access) grants the app the
 * privilege required to query active media sessions via MediaSessionManager.
 * No notification handling logic is needed here.
 */
class PlaybackNotificationListener : NotificationListenerService()
