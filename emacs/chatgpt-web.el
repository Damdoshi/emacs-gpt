;;; chatgpt-web.el --- ChatGPT website in terminal Emacs -*- lexical-binding: t; -*-

;; Version: 0.1.0
;; Package-Requires: ((emacs "28.1"))
;; SPDX-License-Identifier: GPL-3.0-or-later

;;; Commentary:
;; A first-party-to-this-project client, using a local Python/Chromium companion.
;; No ELPA package, API key, TRAMP connection or graphical Emacs is required.
;; See /usr/share/doc/emacs-chatgpt-web/README.fr.md for authentication limits.

;;; Code:

(require 'cl-lib)
(require 'json)
(require 'button)
(require 'subr-x)
(require 'tabulated-list)
(require 'compile)

(defgroup chatgpt-web nil "ChatGPT dans Emacs via le site web." :group 'applications)

(defcustom chatgpt-web-program "chatgpt-web"
  "Programme compagnon installé par le paquet Debian."
  :type 'file)

(defcustom chatgpt-web-program-arguments nil
  "Arguments du compagnon, avant la sous-commande bridge.
Exemple : (\"--profile\" \"/chemin/vers/profil\"). Ne mettez aucun secret ici."
  :type '(repeat string))

(defcustom chatgpt-web-interpreters
  '(("sh" "sh") ("shell" "sh") ("bash" "bash") ("zsh" "zsh")
    ("python" "python3") ("python3" "python3") ("py" "python3")
    ("javascript" "node") ("js" "node") ("perl" "perl") ("ruby" "ruby"))
  "Interpréteurs proposés pour les blocs exécutés explicitement.
Chaque entrée contient une langue suivie du programme et de ses arguments."
  :type '(repeat (repeat string)))

(defvar chatgpt-web-working-directory
  (file-name-as-directory
   (expand-file-name
    (or (and (boundp 'command-line-default-directory)
             command-line-default-directory) default-directory)))
  "Dossier du lancement d'Emacs, puis dernier dossier explicitement choisi.
Ce défaut est commun aux conversations et dure pendant cette session Emacs.")

(defvar chatgpt-web--process nil)
(defvar chatgpt-web--next-id 0)
(defvar chatgpt-web--pending (make-hash-table :test #'eql))
(defvar-local chatgpt-web--snapshot nil)
(defvar-local chatgpt-web--url nil)
(defvar-local chatgpt-web--busy nil)
(defvar-local chatgpt-web--conversation-buffer nil)
(defvar-local chatgpt-web--auth-state nil)

(defun chatgpt-web--start ()
  "Démarrer le compagnon local, sans ouvrir de fenêtre ni de port."
  (unless (process-live-p chatgpt-web--process)
    (unless (executable-find chatgpt-web-program)
      (user-error "Compagnon introuvable : %s" chatgpt-web-program))
    (setq chatgpt-web--process
          (make-process :name "chatgpt-web" :connection-type 'pipe
                        :command (append (list chatgpt-web-program)
                                         chatgpt-web-program-arguments '("bridge"))
                        :coding 'utf-8-unix :noquery t
                        :stderr (get-buffer-create " *ChatGPT compagnon*")
                        :filter #'chatgpt-web--filter
                        :sentinel #'chatgpt-web--sentinel))))

(defun chatgpt-web--request (method params callback &optional buffer)
  "Envoyer METHOD avec PARAMS et appeler CALLBACK avec résultat, erreur, tampon."
  (chatgpt-web--start)
  (let ((id (cl-incf chatgpt-web--next-id)))
    (puthash id (list :callback callback :buffer (or buffer (current-buffer)) :method method)
             chatgpt-web--pending)
    (condition-case error-data
        (process-send-string
         chatgpt-web--process
         (concat (json-serialize (list :id id :method method
                                       :params (or params (make-hash-table)))
                                 :null-object nil :false-object :false)
                 "\n"))
      (error (remhash id chatgpt-web--pending)
             (signal (car error-data) (cdr error-data))))
    id))

(defun chatgpt-web--filter (process chunk)
  "Assembler les lignes JSON de PROCESS reçues dans CHUNK."
  (let ((pending (concat (or (process-get process 'partial) "") chunk)))
    (while (string-match "\n" pending)
      (let ((line (substring pending 0 (match-beginning 0))))
        (setq pending (substring pending (match-end 0)))
        (unless (string-empty-p line)
          (condition-case error-data
              (chatgpt-web--receive
               (json-parse-string line :object-type 'plist :array-type 'list
                                  :null-object nil :false-object nil))
            (error (message "ChatGPT : %s" (error-message-string error-data)))))))
    (process-put process 'partial pending)))

(defun chatgpt-web--receive (message-data)
  "Distribuer MESSAGE-DATA sans afficher automatiquement les réponses."
  (let* ((id (plist-get message-data :id))
         (entry (gethash id chatgpt-web--pending))
         (event (plist-get message-data :event))
         (buffer (plist-get entry :buffer)))
    (cond
     ((equal event "snapshot")
      (when (buffer-live-p buffer)
        (with-current-buffer buffer
          (chatgpt-web--update (plist-get message-data :data)))))
     ((and entry (not event))
      (remhash id chatgpt-web--pending)
      (funcall (plist-get entry :callback)
               (plist-get message-data :result) (plist-get message-data :error) buffer)))))

(defun chatgpt-web--sentinel (process _event)
  "Signaler la fin de PROCESS à toutes ses requêtes en attente."
  (when (and (eq process chatgpt-web--process)
             (memq (process-status process) '(exit signal failed)))
    (setq chatgpt-web--process nil)
    (let ((entries (hash-table-values chatgpt-web--pending)))
      (clrhash chatgpt-web--pending)
      (dolist (entry entries)
        (funcall (plist-get entry :callback) nil
                 '(:code "companion_closed" :message "Le compagnon s'est arrêté.")
                 (plist-get entry :buffer))))))

(defun chatgpt-web--idle ()
  "Refuser une navigation pendant un envoi de message."
  (when (cl-find "send" (hash-table-values chatgpt-web--pending)
                 :key (lambda (entry) (plist-get entry :method)) :test #'equal)
    (user-error "Une réponse est en cours ; attendez ou utilisez la commande d'arrêt")))

(defun chatgpt-web--error (error-data)
  (message "ChatGPT : %s" (plist-get error-data :message)))

(defun chatgpt-web--snapshot-result (result error-data buffer)
  (when (buffer-live-p buffer)
    (with-current-buffer buffer
      (setq chatgpt-web--busy nil)
      (if error-data
          (progn
            (let ((inhibit-read-only t))
              (goto-char (point-max))
              (insert "\n[Erreur] " (plist-get error-data :message) "\n")
              (set-buffer-modified-p nil))
            (chatgpt-web--error error-data)
            (force-mode-line-update))
        (chatgpt-web--update result)))))

(defun chatgpt-web--update (snapshot)
  "Afficher SNAPSHOT dans le tampon courant, y compris en arrière-plan."
  (setq chatgpt-web--snapshot snapshot)
  (let ((url (plist-get snapshot :url)))
    (when (and url (string-match-p "/c/" url))
      (setq chatgpt-web--url url)))
  (let* ((title (replace-regexp-in-string "[\n\r]" " " (or (plist-get snapshot :title) "conversation")))
         (old-point (point))
         (at-end (>= (point) (1- (point-max))))
         (inhibit-read-only t))
    (rename-buffer (concat "*ChatGPT " (truncate-string-to-width title 65) "*") t)
    (erase-buffer)
    (insert "m : écrire   g : actualiser   u : joindre   d : télécharger   w : dossier\n"
            "e : exécuter le bloc   p : appliquer le patch   s : arrêter\n\n")
    (dolist (message-data (plist-get snapshot :messages))
      (insert (propertize (concat (upcase (plist-get message-data :role)) "\n") 'face 'bold))
      (dolist (block (plist-get message-data :blocks))
        (if (equal (plist-get block :type) "code")
            (let ((start (point))
                  (lang (or (plist-get block :language) "")))
              (insert (propertize (concat "── " lang " ──\n") 'face 'shadow))
              (insert (plist-get block :text))
              (unless (bolp) (insert "\n"))
              (add-text-properties start (point) (list 'chatgpt-web-block block))
              (insert-text-button "[Exécuter]" 'follow-link t 'chatgpt-web-block block
                                  'action (lambda (_) (chatgpt-web-execute-block block nil)))
              (insert " ")
              (insert-text-button "[Appliquer le patch]" 'follow-link t 'chatgpt-web-block block
                                  'action (lambda (_) (chatgpt-web-apply-patch-block block nil)))
              (insert " ")
              (insert-text-button "[Enregistrer]" 'follow-link t 'chatgpt-web-block block
                                  'action (lambda (_) (chatgpt-web-save-block block)))
              (insert "\n\n"))
          (insert (plist-get block :text) "\n\n")))
      (insert "\n"))
    (when-let ((files (plist-get snapshot :files)))
      (insert (propertize "FICHIERS\n" 'face 'bold))
      (dolist (file files)
        (insert-text-button (plist-get file :label) 'follow-link t
                            'action (lambda (_) (chatgpt-web--download-file file)))
        (when (string-match-p "\\.\\(?:patch\\|diff\\)\\'" (plist-get file :label))
          (insert " ")
          (insert-text-button "[Télécharger et appliquer]" 'follow-link t
                              'action (lambda (_) (chatgpt-web--download-file file t))))
        (insert "\n")))
    (goto-char (if at-end (point-max) (min old-point (point-max))))
    (set-buffer-modified-p nil)
    (force-mode-line-update)))

(defvar chatgpt-web-mode-map
  (let ((map (make-sparse-keymap)))
    (set-keymap-parent map special-mode-map)
    (define-key map (kbd "m") #'chatgpt-web-compose)
    (define-key map (kbd "g") #'chatgpt-web-refresh)
    (define-key map (kbd "u") #'chatgpt-web-upload)
    (define-key map (kbd "d") #'chatgpt-web-download)
    (define-key map (kbd "w") #'chatgpt-web-set-directory)
    (define-key map (kbd "e") #'chatgpt-web-execute-block)
    (define-key map (kbd "p") #'chatgpt-web-apply-patch-block)
    (define-key map (kbd "s") #'chatgpt-web-stop)
    (define-key map (kbd "C-c C-n") #'chatgpt-web-new)
    map))

(define-derived-mode chatgpt-web-mode special-mode "ChatGPT"
  "Conversation du site ChatGPT, utilisable en terminal."
  (setq-local header-line-format
              '(:eval (format "%s | dossier : %s | %s" (system-name)
                              chatgpt-web-working-directory
                              (if chatgpt-web--busy "réponse en cours…" "disponible")))))

(defun chatgpt-web--open-buffer (url)
  (chatgpt-web--idle)
  (let ((buffer (generate-new-buffer "*ChatGPT chargement*")))
    (with-current-buffer buffer
      (chatgpt-web-mode)
      (setq chatgpt-web--url url chatgpt-web--busy t)
      (let ((inhibit-read-only t)) (insert "Chargement de la conversation…\n")))
    (pop-to-buffer buffer)
    (chatgpt-web--request "open" (when url (list :url url))
                          #'chatgpt-web--snapshot-result buffer)))

;;;###autoload
(defun chatgpt-web ()
  "Ouvrir le menu ChatGPT Web."
  (interactive)
  (pcase (completing-read "ChatGPT : " '("Nouvelle conversation" "Conversations" "Ouvrir une URL" "Connexion") nil t)
    ("Nouvelle conversation" (chatgpt-web-new))
    ("Conversations" (chatgpt-web-conversations))
    ("Ouvrir une URL" (call-interactively #'chatgpt-web-open))
    ("Connexion" (chatgpt-web-login))))

;;;###autoload
(defun chatgpt-web-new ()
  "Créer un tampon pour une nouvelle conversation du site."
  (interactive)
  (chatgpt-web--open-buffer nil))

;;;###autoload
(defun chatgpt-web-open (url)
  "Reprendre la conversation personnelle à URL."
  (interactive "sURL https://chatgpt.com/c/… ou identifiant : ")
  (chatgpt-web--open-buffer (string-trim url)))

(defun chatgpt-web-refresh ()
  "Actualiser la conversation courante."
  (interactive)
  (chatgpt-web--idle)
  (setq chatgpt-web--busy t)
  (chatgpt-web--request "snapshot" (when chatgpt-web--url (list :url chatgpt-web--url))
                        #'chatgpt-web--snapshot-result))

(defun chatgpt-web-stop ()
  "Demander au site d'arrêter la génération courante."
  (interactive)
  (chatgpt-web--request "stop" nil
                        (lambda (_result error-data _buffer)
                          (if error-data (chatgpt-web--error error-data)
                            (message "Demande d'arrêt envoyée")))))

(defvar chatgpt-web-compose-mode-map
  (let ((map (make-sparse-keymap)))
    (define-key map (kbd "C-c C-c") #'chatgpt-web-send)
    (define-key map (kbd "C-c C-k") #'bury-buffer)
    map))

(define-minor-mode chatgpt-web-compose-mode
  "Rédaction : C-c C-c envoie ; C-c C-k masque le brouillon."
  :lighter " ChatGPT-rédaction" :keymap chatgpt-web-compose-mode-map)

(defun chatgpt-web-compose ()
  "Rédiger une réponse dans un tampon séparé."
  (interactive)
  (unless (derived-mode-p 'chatgpt-web-mode)
    (user-error "Ouvrez d'abord une conversation"))
  (let* ((conversation (current-buffer))
         (buffer (get-buffer-create (concat "*Rédaction " (buffer-name conversation) "*"))))
    (with-current-buffer buffer
      (unless chatgpt-web-compose-mode
        (text-mode)
        (chatgpt-web-compose-mode 1))
      (setq chatgpt-web--conversation-buffer conversation)
      (setq-local default-directory chatgpt-web-working-directory)
      (setq-local header-line-format "C-c C-c : envoyer | C-c C-k : masquer le brouillon"))
    (pop-to-buffer buffer)))

(defun chatgpt-web-send ()
  "Envoyer le brouillon courant, puis laisser la réponse arriver en arrière-plan."
  (interactive)
  (let ((conversation chatgpt-web--conversation-buffer)
        (draft (current-buffer))
        (text (buffer-substring-no-properties (point-min) (point-max))))
    (unless (buffer-live-p conversation) (user-error "La conversation a été fermée"))
    (when (string-empty-p (string-trim text)) (user-error "Le message est vide"))
    (chatgpt-web--idle)
    (with-current-buffer conversation
      (setq chatgpt-web--busy t)
      (force-mode-line-update)
      (chatgpt-web--request
       "send" (list :text text :url chatgpt-web--url)
       (lambda (result error-data buffer)
         (chatgpt-web--snapshot-result result error-data buffer)
         (unless error-data
           (when (buffer-live-p draft)
             (with-current-buffer draft
               ;; Keep any new text the user wrote while this response was arriving.
               (when (equal text (buffer-substring-no-properties (point-min) (point-max)))
                 (erase-buffer)
                 (set-buffer-modified-p nil))))
           (message "Réponse ChatGPT reçue")))
       conversation))
    (bury-buffer draft)))

(define-derived-mode chatgpt-web-list-mode tabulated-list-mode "Conversations-ChatGPT"
  "Liste des conversations chargées dans la barre latérale du site."
  (setq tabulated-list-format [("Titre" 55 t) ("URL" 65 nil)])
  (setq tabulated-list-padding 2)
  (tabulated-list-init-header)
  (local-set-key (kbd "RET") #'chatgpt-web--list-open))

(defun chatgpt-web--list-open ()
  (interactive)
  (when-let ((url (tabulated-list-get-id))) (chatgpt-web-open url)))

;;;###autoload
(defun chatgpt-web-conversations ()
  "Lister les conversations chargées ; RET en reprend une."
  (interactive)
  (chatgpt-web--idle)
  (let ((buffer (get-buffer-create "*Conversations ChatGPT*")))
    (with-current-buffer buffer
      (chatgpt-web-list-mode)
      (setq-local header-line-format "Conversations chargées par le site. Autre conversation : M-x chatgpt-web-open"))
    (pop-to-buffer buffer)
    (chatgpt-web--request
     "conversations" nil
     (lambda (result error-data target)
       (if error-data (chatgpt-web--error error-data)
         (when (buffer-live-p target)
           (with-current-buffer target
             (setq tabulated-list-entries
                   (mapcar (lambda (item)
                             (list (plist-get item :url)
                                   (vector (plist-get item :title) (plist-get item :url))))
                           (plist-get result :conversations)))
             (tabulated-list-print t))))) buffer)))

(defun chatgpt-web-set-directory (directory)
  "Mémoriser DIRECTORY pour les prochains scripts, patches et transferts."
  (interactive (list (read-directory-name "Dossier de travail : " chatgpt-web-working-directory nil t)))
  (when (file-remote-p directory) (user-error "Choisissez un dossier local à la machine où tourne Emacs"))
  (unless (file-directory-p directory) (user-error "Ce dossier n'existe pas"))
  (setq chatgpt-web-working-directory (file-name-as-directory (expand-file-name directory)))
  (force-mode-line-update t)
  (message "Dossier de travail : %s" chatgpt-web-working-directory))

(defun chatgpt-web--directory (prompt)
  (when prompt (call-interactively #'chatgpt-web-set-directory))
  (unless (file-directory-p chatgpt-web-working-directory) (user-error "Le dossier de travail n'existe plus"))
  chatgpt-web-working-directory)

(defun chatgpt-web-upload (file)
  "Joindre FILE au prochain message de la conversation."
  (interactive (list (read-file-name "Fichier à joindre : " chatgpt-web-working-directory nil t)))
  (chatgpt-web--idle)
  (when (file-remote-p file) (user-error "Le fichier doit être local à cette machine"))
  (chatgpt-web--request
   "upload" (list :paths (vector (expand-file-name file)) :url chatgpt-web--url)
   (lambda (result error-data _buffer)
     (if error-data (chatgpt-web--error error-data)
       (message "%s" (plist-get result :note))))))

(defun chatgpt-web--download-file (file &optional apply-patch)
  (chatgpt-web--idle)
  (let* ((label (file-name-nondirectory (plist-get file :label)))
         (directory chatgpt-web-working-directory)
         (destination (read-file-name "Télécharger vers : " chatgpt-web-working-directory nil nil label)))
    (when (file-remote-p destination) (user-error "La destination doit être locale à cette machine"))
    (when (file-exists-p destination) (user-error "La destination existe déjà"))
    (chatgpt-web--request
     "download" (list :ref (plist-get file :ref) :href (plist-get file :href)
                       :destination (expand-file-name destination) :url chatgpt-web--url)
     (lambda (result error-data _buffer)
       (if error-data (chatgpt-web--error error-data)
         (message "Fichier téléchargé : %s" (plist-get result :path))
         (when apply-patch
           (let ((chatgpt-web-working-directory directory))
             (chatgpt-web-apply-patch-file (plist-get result :path)))))))))

(defun chatgpt-web-download ()
  "Choisir un fichier disponible dans la conversation et le télécharger."
  (interactive)
  (let* ((files (plist-get chatgpt-web--snapshot :files))
         (choices (cl-loop for file in files for index from 1
                           collect (cons (format "%d. %s" index (plist-get file :label)) file))))
    (unless choices (user-error "Aucun fichier reconnu ; actualisez éventuellement la conversation"))
    (chatgpt-web--download-file (cdr (assoc (completing-read "Fichier : " choices nil t) choices)))))

(defun chatgpt-web--auth-result (result error-data buffer)
  (if error-data (chatgpt-web--error error-data)
    (when (buffer-live-p buffer)
      (with-current-buffer buffer
        (setq chatgpt-web--auth-state result)
        (let ((inhibit-read-only t))
          (erase-buffer)
          (insert (plist-get result :url) "\n" (plist-get result :title) "\n\n"
                  (plist-get result :text) "\n\n")
          (when (plist-get result :challenge)
            (insert "Vérification visuelle demandée par le site. Elle n'est pas contournée.\n\n"))
          (when (plist-get result :connected)
            (insert "Session détectée. Vous pouvez ouvrir une conversation.\n\n"))
          (dolist (control (plist-get result :controls))
            (insert-text-button
             (concat "[" (plist-get control :kind) "] " (plist-get control :label))
             'follow-link t 'action (lambda (_) (chatgpt-web--auth-control control)))
            (insert "\n"))
          (insert "\ng : actualiser ; q : masquer. Les secrets ne sont pas enregistrés.\n")
          (goto-char (point-min))
          (set-buffer-modified-p nil))))))

(defun chatgpt-web--auth-control (control)
  (let* ((kind (plist-get control :kind))
         (input-type (plist-get control :input_type))
         (label (plist-get control :label))
         (value (when (member kind '("input" "select"))
                  (cond ((member input-type '("checkbox" "radio"))
                         (if (y-or-n-p (concat label " ? ")) t :false))
                        ((equal input-type "email") (read-string (concat label " : ")))
                        (t (read-passwd (concat label " : ")))))))
    (unwind-protect
        (chatgpt-web--request "auth_action" (list :ref (plist-get control :ref) :value value)
                              #'chatgpt-web--auth-result)
      (when (and (stringp value) (not (equal input-type "email"))) (clear-string value)))))

(defun chatgpt-web--auth-refresh ()
  (interactive)
  (chatgpt-web--request "auth_state" nil #'chatgpt-web--auth-result))

;;;###autoload
(defun chatgpt-web-login ()
  "Afficher les étapes de connexion du vrai navigateur dans un tampon terminal."
  (interactive)
  (chatgpt-web--idle)
  (let ((buffer (get-buffer-create "*Connexion ChatGPT*")))
    (with-current-buffer buffer
      (special-mode)
      (local-set-key (kbd "g") #'chatgpt-web--auth-refresh))
    (pop-to-buffer buffer)
    (chatgpt-web--request "auth_begin" nil #'chatgpt-web--auth-result buffer)))

(defun chatgpt-web-disconnect ()
  "Arrêter le compagnon et Chromium, en conservant le profil de session."
  (interactive)
  (when (process-live-p chatgpt-web--process)
    (ignore-errors (process-send-eof chatgpt-web--process))))

(add-hook 'kill-emacs-hook #'chatgpt-web-disconnect)

;;; Local actions are below; they are never called by incoming website messages.

(defun chatgpt-web--block-at-point ()
  (or (get-text-property (point) 'chatgpt-web-block)
      (get-text-property (max (point-min) (1- (point))) 'chatgpt-web-block)
      (user-error "Placez le curseur dans un bloc de code")))

(defun chatgpt-web-save-block (block)
  "Enregistrer le contenu exact de BLOCK dans un fichier choisi."
  (interactive (list (chatgpt-web--block-at-point)))
  (let ((file (read-file-name "Enregistrer le bloc : " chatgpt-web-working-directory)))
    (when (file-exists-p file) (user-error "Le fichier existe déjà"))
    (write-region (plist-get block :text) nil file nil 'silent nil 'excl)
    (message "Bloc enregistré : %s" file)))

(defun chatgpt-web--local-process (name command directory &optional cleanup after-success)
  "Lancer COMMAND dans DIRECTORY, puis nettoyer CLEANUP et appeler AFTER-SUCCESS."
  (let* ((buffer (generate-new-buffer (concat "*ChatGPT " name "*")))
         (default-directory directory))
    (with-current-buffer buffer
      (compilation-mode)
      (setq-local default-directory directory)
      (let ((inhibit-read-only t))
        (insert (format "Machine : %s\nDossier : %s\nCommande : %s\n\n"
                        (system-name) directory (mapconcat #'shell-quote-argument command " ")))))
    (condition-case error-data
        (make-process
         :name (concat "chatgpt-web-" name) :buffer buffer :stderr buffer
         :command command :connection-type 'pipe :coding 'utf-8-unix :noquery nil
         :filter (lambda (process output)
                   (when (buffer-live-p (process-buffer process))
                     (with-current-buffer (process-buffer process)
                       (let ((inhibit-read-only t))
                         (goto-char (point-max)) (insert output)))))
         :sentinel (lambda (process _event)
                     (when (memq (process-status process) '(exit signal failed))
                       (when cleanup (ignore-errors (delete-file cleanup)))
                       (when (and after-success (eq (process-status process) 'exit)
                                  (= (process-exit-status process) 0))
                         (condition-case error-data (funcall after-success)
                           (error (message "ChatGPT : %s" (error-message-string error-data)))))
                       (when (buffer-live-p (process-buffer process))
                         (with-current-buffer (process-buffer process)
                           (let ((inhibit-read-only t))
                             (goto-char (point-max))
                             (insert (format "\nCode de retour : %d\n" (process-exit-status process)))))))))
      (error (when cleanup (ignore-errors (delete-file cleanup)))
             (signal (car error-data) (cdr error-data))))
    (display-buffer buffer)
    buffer))

(defun chatgpt-web-execute-block (block prompt-directory)
  "Exécuter BLOCK explicitement ; avec préfixe, choisir un nouveau dossier."
  (interactive (list (chatgpt-web--block-at-point) current-prefix-arg))
  (let* ((directory (chatgpt-web--directory prompt-directory))
         (language (downcase (or (plist-get block :language) "")))
         (interpreter (cdr (assoc language chatgpt-web-interpreters))))
    (unless interpreter
      (setq interpreter (split-string-and-unquote (read-shell-command "Interpréteur et arguments : " "sh"))))
    (unless (and interpreter (executable-find (car interpreter)))
      (user-error "Interpréteur introuvable"))
    (let ((script (make-temp-file "chatgpt-web-script-")))
      (let ((coding-system-for-write 'utf-8-unix))
        (write-region (plist-get block :text) nil script nil 'silent))
      (chatgpt-web--local-process "script" (append interpreter (list script)) directory script))))

(defun chatgpt-web--unsaved-files (directory)
  (cl-loop for buffer in (buffer-list)
           when (with-current-buffer buffer
                  (and buffer-file-name (buffer-modified-p)
                       (file-in-directory-p buffer-file-name directory)))
           collect (buffer-name buffer)))

(defun chatgpt-web--refresh-files (directory)
  "Relire les fichiers changés dans DIRECTORY en préservant les nouveaux edits."
  (dolist (buffer (buffer-list))
    (with-current-buffer buffer
      (when (and buffer-file-name (not (file-remote-p buffer-file-name))
                 (file-in-directory-p buffer-file-name directory)
                 (not (verify-visited-file-modtime buffer)))
        (cond ((buffer-modified-p)
               (message "Fichier changé sur disque ; modifications Emacs conservées : %s" buffer-file-name))
              ((file-exists-p buffer-file-name) (revert-buffer t t t))
              (t (message "Fichier supprimé par le patch : %s" buffer-file-name)))))))

(defun chatgpt-web-apply-patch-file (file &optional prompt-directory)
  "Vérifier et appliquer FILE ; avec préfixe, choisir le dossier de travail."
  (interactive (list (read-file-name "Patch à appliquer : " chatgpt-web-working-directory nil t)
                     current-prefix-arg))
  (let* ((directory (chatgpt-web--directory prompt-directory))
         (unsaved (chatgpt-web--unsaved-files directory)))
    (when unsaved
      (user-error "Enregistrez les fichiers modifiés dans Emacs avant le patch : %s" (string-join unsaved ", ")))
    (when (file-remote-p file) (user-error "Choisissez un patch local à cette machine"))
    (chatgpt-web--local-process
     "patch" (append (list chatgpt-web-program) chatgpt-web-program-arguments
                      (list "apply-patch" (expand-file-name file) "--directory" directory))
     directory nil (lambda () (chatgpt-web--refresh-files directory)))))

(defun chatgpt-web-apply-patch-block (block prompt-directory)
  "Vérifier puis appliquer BLOCK comme patch ; avec préfixe, choisir le dossier."
  (interactive (list (chatgpt-web--block-at-point) current-prefix-arg))
  (let* ((directory (chatgpt-web--directory prompt-directory))
         (unsaved (chatgpt-web--unsaved-files directory)))
    (when unsaved (user-error "Enregistrez les fichiers modifiés dans Emacs avant le patch : %s" (string-join unsaved ", ")))
    (let ((patch (make-temp-file "chatgpt-web-patch-" nil ".patch")))
      (let ((coding-system-for-write 'utf-8-unix))
        (let ((text (plist-get block :text)))
          (write-region (if (string-suffix-p "\n" text) text (concat text "\n")) nil patch nil 'silent)))
      (chatgpt-web--local-process
       "patch" (append (list chatgpt-web-program) chatgpt-web-program-arguments
                        (list "apply-patch" patch "--directory" directory)) directory patch
       (lambda () (chatgpt-web--refresh-files directory))))))

(provide 'chatgpt-web)
;;; chatgpt-web.el ends here
