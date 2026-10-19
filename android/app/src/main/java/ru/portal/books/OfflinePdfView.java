package ru.portal.books;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.graphics.pdf.PdfRenderer;
import android.os.ParcelFileDescriptor;
import android.view.Gravity;
import android.widget.Button;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.io.IOException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Native PDF pages, available without WebView or a network connection. */
final class OfflinePdfView extends LinearLayout {
    private final ExecutorService rendererThread = Executors.newSingleThreadExecutor();
    private final OfflineBooks store;
    private final OfflineBooks.Book book;
    private final ImageView image;
    private final TextView counter;
    private final Button previous;
    private final Button next;
    private PdfRenderer renderer; // Accessed only on rendererThread.
    private ParcelFileDescriptor descriptor;
    private Bitmap displayed;
    private volatile boolean closed;
    private volatile int generation;
    private int current;
    private int count;
    private int width;
    private int height;

    OfflinePdfView(Context context, OfflineBooks store, OfflineBooks.Book book, Runnable back) {
        super(context);
        this.store = store;
        this.book = book;
        current = book.page;
        setOrientation(VERTICAL);
        setBackgroundColor(Color.rgb(251,247,238));
        Button close = button("← Сохранённые книги");
        close.setOnClickListener(view -> back.run());
        addView(close, new LayoutParams(-1, -2));
        TextView title = new TextView(context);
        title.setText(book.title + " · PDF без озвучки");
        title.setTextColor(Color.rgb(43,63,67));
        title.setTextSize(16);
        title.setGravity(Gravity.CENTER);
        title.setPadding(12, 4, 12, 8);
        addView(title, new LayoutParams(-1, -2));
        image = new ImageView(context);
        image.setScaleType(ImageView.ScaleType.FIT_CENTER);
        image.setContentDescription("Страница книги");
        addView(image, new LayoutParams(-1, 0, 1));
        LinearLayout controls = new LinearLayout(context);
        controls.setGravity(Gravity.CENTER_VERTICAL);
        previous = button("← Назад");
        previous.setEnabled(false);
        previous.setOnClickListener(view -> { if (current > 0) { current--; render(); } });
        controls.addView(previous, new LayoutParams(0, -2, 1));
        counter = new TextView(context);
        counter.setText("Открываем PDF…");
        counter.setGravity(Gravity.CENTER);
        counter.setTextColor(Color.rgb(43,63,67));
        controls.addView(counter, new LayoutParams(0, -2, 1));
        next = button("Вперёд →");
        next.setEnabled(false);
        next.setOnClickListener(view -> { if (current + 1 < count) { current++; render(); } });
        controls.addView(next, new LayoutParams(0, -2, 1));
        addView(controls, new LayoutParams(-1, -2));
        image.addOnLayoutChangeListener((view, left, top, right, bottom, oldLeft, oldTop, oldRight, oldBottom) -> {
            int newWidth = right - left;
            int newHeight = bottom - top;
            if (newWidth != width || newHeight != height) {
                width = newWidth;
                height = newHeight;
                render();
            }
        });
        rendererThread.execute(() -> {
            try {
                descriptor = ParcelFileDescriptor.open(book.file, ParcelFileDescriptor.MODE_READ_ONLY);
                renderer = new PdfRenderer(descriptor);
                int pages = renderer.getPageCount();
                post(() -> {
                    if (closed) return;
                    count = pages;
                    current = Math.min(Math.max(0, current), pages - 1);
                    render();
                });
            } catch (IOException | RuntimeException error) {
                post(() -> { if (!closed) counter.setText("Не удалось открыть PDF. Удалите файл и сохраните книгу заново."); });
            }
        });
    }

    private Button button(String text) {
        Button result = new Button(getContext());
        result.setAllCaps(false);
        result.setText(text);
        return result;
    }

    private void render() {
        if (closed || width < 1 || height < 1 || count < 1) return;
        final int token = ++generation;
        final int pageNumber = current;
        final int fitWidth = Math.min(width, 2048);
        final int fitHeight = Math.min(height, 3072);
        previous.setEnabled(current > 0);
        next.setEnabled(current + 1 < count);
        counter.setText((current + 1) + " / " + count);
        rendererThread.execute(() -> {
            if (closed || token != generation || renderer == null) return;
            Bitmap bitmap = null;
            try (PdfRenderer.Page page = renderer.openPage(pageNumber)) {
                float scale = Math.min((float) fitWidth / page.getWidth(), (float) fitHeight / page.getHeight());
                bitmap = Bitmap.createBitmap(Math.max(1, Math.round(page.getWidth() * scale)), Math.max(1, Math.round(page.getHeight() * scale)), Bitmap.Config.ARGB_8888);
                bitmap.eraseColor(Color.WHITE);
                page.render(bitmap, null, null, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY);
                if (closed || token != generation) { bitmap.recycle(); return; }
                store.setPage(book, pageNumber);
                Bitmap result = bitmap;
                post(() -> {
                    if (closed || token != generation) { result.recycle(); return; }
                    Bitmap old = displayed;
                    displayed = result;
                    image.setImageBitmap(result);
                    image.setContentDescription("Страница " + (pageNumber + 1) + " из " + count);
                    if (old != null) old.recycle();
                });
            } catch (IOException | RuntimeException | OutOfMemoryError error) {
                if (bitmap != null && !bitmap.isRecycled()) bitmap.recycle();
                post(() -> { if (!closed && token == generation) counter.setText("Не удалось показать страницу"); });
            }
        });
    }

    void close() {
        if (closed) return;
        closed = true;
        generation++;
        image.setImageDrawable(null);
        if (displayed != null) { displayed.recycle(); displayed = null; }
        rendererThread.execute(() -> {
            if (renderer != null) { renderer.close(); renderer = null; }
            else if (descriptor != null) { try { descriptor.close(); } catch (IOException ignored) { } }
            descriptor = null;
        });
        rendererThread.shutdown();
    }
}
