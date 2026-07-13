package com.apituner.agent

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle

/** Opens the system uninstall dialog for a package (user confirms). */
class UninstallActivity : Activity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val pkg = intent.getStringExtra("packageName")
        if (pkg == null) {
            finish()
            return
        }
        try {
            val intent = Intent(Intent.ACTION_DELETE).apply {
                data = Uri.parse("package:$pkg")
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            startActivity(intent)
        } catch (_: Exception) {
            // ignore
        }
        finish()
    }
}
