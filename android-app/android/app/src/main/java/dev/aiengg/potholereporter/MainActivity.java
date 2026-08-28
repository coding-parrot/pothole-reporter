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
    public interface PreviewAttachmentCallback {
        void onComplete(boolean attached);
    }

    private FrameLayout drivePreviewHost;
    private PreviewView drivePreview;
    private long drivePreviewRequestGeneration = 0L;

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
        double viewportHeight,
        PreviewAttachmentCallback callback
    ) {
        runOnUiThread(() -> {
            final long requestGeneration = ++drivePreviewRequestGeneration;
            if (drivePreviewHost == null || drivePreview == null || getBridge() == null) {
                callback.onComplete(false);
                return;
            }
            WebView webView = getBridge().getWebView();
            // Activity recreation can reach the bridge one frame before WebView layout.
            // Resolve only after a real surface attachment; JavaScript otherwise marks a
            // permanently blank Preview as attached and never retries it.
            webView.post(() -> {
                if (requestGeneration != drivePreviewRequestGeneration || isFinishing() || isDestroyed() ||
                    webView.getWidth() <= 0 || webView.getHeight() <= 0 ||
                    viewportWidth <= 0 || viewportHeight <= 0) {
                    callback.onComplete(false);
                    return;
                }
                double scaleX = webView.getWidth() / viewportWidth;
                double scaleY = webView.getHeight() / viewportHeight;
                ViewGroup.LayoutParams params = drivePreviewHost.getLayoutParams();
                params.width = Math.max(1, (int) Math.round(cssWidth * scaleX));
                params.height = Math.max(1, (int) Math.round(cssHeight * scaleY));
                drivePreviewHost.setLayoutParams(params);
                drivePreviewHost.setX(webView.getX() + (float) (cssLeft * scaleX));
                drivePreviewHost.setY(webView.getY() + (float) (cssTop * scaleY));
                // PreviewView cannot fulfil CameraX's SurfaceRequest while an ancestor is
                // GONE. The old order bound Preview first and made the host visible only
                // after binding reported success, creating a circular wait that ended in
                // CameraX's five-second session-configuration timeout. Make and lay out
                // the surface before registering it with the service.
                drivePreviewHost.setVisibility(View.VISIBLE);
                drivePreviewHost.requestLayout();
                attachDrivePreviewWhenLaidOut(requestGeneration, 60, callback);
            });
        });
    }

    private void attachDrivePreviewWhenLaidOut(
        long requestGeneration,
        int attemptsRemaining,
        PreviewAttachmentCallback callback
    ) {
        if (requestGeneration != drivePreviewRequestGeneration || isFinishing() || isDestroyed() ||
            drivePreviewHost == null || drivePreview == null) {
            callback.onComplete(false);
            return;
        }
        if (!drivePreviewHost.isAttachedToWindow() || !drivePreviewHost.isLaidOut() ||
            drivePreviewHost.getWidth() <= 0 || drivePreviewHost.getHeight() <= 0) {
            if (attemptsRemaining <= 0) {
                drivePreviewHost.setVisibility(View.GONE);
                callback.onComplete(false);
                return;
            }
            drivePreviewHost.postOnAnimation(() ->
                attachDrivePreviewWhenLaidOut(requestGeneration, attemptsRemaining - 1, callback)
            );
            return;
        }

        DriveForegroundService service = DriveForegroundService.Companion.getActiveService();
        if (service == null) {
            drivePreviewHost.setVisibility(View.GONE);
            callback.onComplete(false);
            return;
        }
        Boolean attachment = service.attachPreview(drivePreview.getSurfaceProvider());
        if (Boolean.FALSE.equals(attachment)) {
            drivePreviewHost.setVisibility(View.GONE);
        }
        // null means CameraX accepted this exact, now-laid-out surface while its provider
        // is still starting. Keep the host visible so the pending SurfaceRequest can be
        // fulfilled, but let JavaScript retry until the graph reports it bound.
        callback.onComplete(Boolean.TRUE.equals(attachment));
    }

    public void hideDrivePreview() {
        runOnUiThread(() -> {
            drivePreviewRequestGeneration++;
            DriveForegroundService service = DriveForegroundService.Companion.getActiveService();
            if (service != null && drivePreview != null) {
                service.detachPreview(drivePreview.getSurfaceProvider());
            }
            if (drivePreviewHost != null) drivePreviewHost.setVisibility(View.GONE);
        });
    }

    @Override
    public void onDestroy() {
        hideDrivePreview();
        super.onDestroy();
    }
}
