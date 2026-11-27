package com.apituner.agent

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.util.Log
import androidx.core.content.FileProvider
import java.io.File

/** Opens the system installer for an uploaded APK (user confirms). */
class InstallActivity : Activity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val path = intent.getStringExtra("apkPath")
        if (path == null) {
            finish()
            return
        }
        try {
            val file = File(path)
            val uri: Uri = FileProvider.getUriForFile(
                this, "$packageName.fileprovider", file
            )
            val install = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                Intent(Intent.ACTION_VIEW).apply {
                    setDataAndType(uri, "application/vnd.android.package-archive")
                    flags = Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK
                }
            } else {
                @Suppress("DEPRECATION")
                Intent(Intent.ACTION_INSTALL_PACKAGE).apply {
                    data = uri
                    flags = Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK
                    putExtra(Intent.EXTRA_NOT_UNKNOWN_SOURCE, true)
                    putExtra(Intent.EXTRA_RETURN_RESULT, true)
                }
            }
            startActivity(install)
        } catch (e: Exception) {
            Log.w(TAG, "Failed to launch APK installer for $path: ${e.message}", e)
        }
        finish()
    }

    companion object {
        private const val TAG = "InstallActivity"
    }
}
