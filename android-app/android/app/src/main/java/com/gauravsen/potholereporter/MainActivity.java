package com.gauravsen.potholereporter;

import android.os.Bundle;
import com.gauravsen.potholereporter.plugin.DriveModePlugin;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(DriveModePlugin.class);
        super.onCreate(savedInstanceState);
    }
}
