# Постоянный ключ Qicko

Для первой публикации создан отдельный ключ RSA-4096, SHA256withRSA, срок 10 000 дней. Alias — **qicko**. Публичный отпечаток находится рядом с APK в файле с суффиксом -certificate.txt.

## Где находятся закрытые файлы

На текущем компьютере внутри Portal:

~~~text
.private/android-signing/qicko-release.jks   — закрытый ключ
.private/android-signing/password.clixml    — пароль, зашифрованный Windows DPAPI
.private/android-signing/signing.json       — alias и имена файлов, без пароля
~~~

Доступ к каталогу ограничен владельцем Windows и SYSTEM. Каталог исключён из Git; упаковщик не включает его в ZIP. Не загружайте его в магазин или публичное облако.

**Для обновлений нужен именно этот JKS и его пароль.** Клонирование GitHub их не восстанавливает. Новый ключ не заменит старый ключ уже выпущенного приложения.

## Сделайте переносимую резервную копию сейчас

1. Скопируйте qicko-release.jks в своё защищённое резервное хранилище.
2. Сохраните alias qicko и пароль в менеджере паролей.
3. Проверьте, что резервная копия открывается ключевым инструментом или Android Studio.
4. Не полагайтесь только на password.clixml: DPAPI привязан к текущему пользователю и компьютеру.

Чтобы посмотреть сгенерированный пароль, самостоятельно выполните на этом компьютере в PowerShell из папки Portal:

~~~powershell
$qickoSecret = Import-Clixml -LiteralPath '.private/android-signing/password.clixml'
[Net.NetworkCredential]::new('', $qickoSecret).Password
$qickoSecret.Dispose()
Remove-Variable qickoSecret
~~~

Пароль появится только в вашем локальном терминале. Сохраните его в менеджер паролей; не присылайте в чат, Git или комментарий RuStore. У этого ключа пароли хранилища и ключа одинаковые.

## Собрать на текущем компьютере

~~~powershell
.\android\build-rustore.ps1
~~~

Скрипт сам читает DPAPI, не печатает пароль и очищает временные переменные окружения.

## Собрать на другом компьютере

Восстановите JKS из личной резервной копии, установите SDK/JDK и выполните:

~~~powershell
.\android\build-rustore.ps1 -KeystorePath 'D:/SecureKeys/qicko-release.jks' -KeyAlias 'qicko'
~~~

Введите пароли скрытым вводом. Либо выберите тот же JKS/alias в Android Studio → Generate Signed APK.

## Создание ключа для другого нового приложения

Для Qicko этот шаг уже выполнен. Не запускайте его ради обновления.

Для действительно нового приложения скрипт умеет однократно создать ключ:

~~~powershell
.\android\create-release-key.ps1 -GeneratePassword
~~~

Он откажется перезаписывать существующий JKS или файл пароля. Без -GeneratePassword пароль запрашивается скрытым вводом. Если ключ нужен для другого приложения, используйте отдельный SigningDirectory и alias.

Справка: [подпись Android и хранение ключа](https://developer.android.com/studio/publish/app-signing).
