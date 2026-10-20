package com.amuejunankar.ytdl

import android.app.Application

class App : Application() {
    override fun onCreate() {
        super.onCreate()
        Engine.init(this)
    }
}
