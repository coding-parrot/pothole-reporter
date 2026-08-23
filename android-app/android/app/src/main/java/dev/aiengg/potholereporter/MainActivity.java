package dev.aiengg.potholereporter;

import android.graphics.Color;
import android.os.Bundle;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.WebView;
import android.widget.FrameLayout;
import androidx.camera.view.PreviewView;
import dev.aiengg.potholereporter.drive.DriveForegroundService;
import dev.aiengg.potholereporter.plugin.DriveModePlugin;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    private FrameLayout drivePreviewHost;
    private PreviewView drivePreview;

    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(DriveModePlugin.class);
        super.onCreate(savedInstanceState);
        createDrivePreview();
    }

    private void createDrivePreview() {
        WebView webView = getBridge().getWebView();
        ViewGroup parent = (ViewGroup) webView.getParent();
        parent.setBackgroundColor(Color.rgb(17, 18, 20));
        webView.setBackgroundColor(Color.TRANSPARENT);

        drivePreview = new PreviewView(this);
        drivePreview.setImplementationMode(PreviewView.ImplementationMode.COMPATIBLE);
        drivePreview.setScaleType(PreviewView.ScaleType.FILL_CENTER);
        drivePreview.setBackgroundColor(Color.BLACK);
        drivePreviewHost = new FrameLayout(this);
        drivePreviewHost.setBackgroundColor(Color.BLACK);
        drivePreviewHost.addView(drivePreview, new FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.MATCH_PARENT
        ));
        drivePreviewHost.setVisibility(View.GONE);
        int webIndex = parent.indexOfChild(webView);
        parent.addView(drivePreviewHost, Math.max(0, webIndex), new ViewGroup.LayoutParams(1, 1));
    }

    public void showDrivePreview(
        double cssLeft,
        double cssTop,
        double cssWidth,
        double cssHeight,
        double viewportWidth,
        double viewportHeight
    ) {
        runOnUiThread(() -> {
            if (drivePreviewHost == null || drivePreview == null || getBridge() == null) return;
            WebView webView = getBridge().getWebView();
            if (webView.getWidth() <= 0 || webView.getHeight() <= 0 || viewportWidth <= 0 || viewportHeight <= 0) return;
            double scaleX = webView.getWidth() / viewportWidth;
            double scaleY = webView.getHeight() / viewportHeight;
            ViewGroup.LayoutParams params = drivePreviewHost.getLayoutParams();
            params.width = Math.max(1, (int) Math.round(cssWidth * scaleX));
            params.height = Math.max(1, (int) Math.round(cssHeight * scaleY));
            drivePreviewHost.setLayoutParams(params);
            drivePreviewHost.setX(webView.getX() + (float) (cssLeft * scaleX));
            drivePreviewHost.setY(webView.getY() + (float) (cssTop * scaleY));
            drivePreviewHost.setVisibility(View.VISIBLE);
            DriveForegroundService service = DriveForegroundService.Companion.getActiveService();
            if (service != null) service.attachPreview(drivePreview.getSurfaceProvider());
        });
    }

    public void hideDrivePreview() {
        runOnUiThread(() -> {
            DriveForegroundService service = DriveForegroundService.Companion.getActiveService();
            if (service != null) service.detachPreview();
            if (drivePreviewHost != null) drivePreviewHost.setVisibility(View.GONE);
        });
    }

    @Override
    public void onPause() {
        hideDrivePreview();
        super.onPause();
    }

    @Override
    public void onDestroy() {
        hideDrivePreview();
        super.onDestroy();
    }
}
