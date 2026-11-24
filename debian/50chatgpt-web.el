;;; Debian site startup for the independently packaged ChatGPT Web mode.
(add-to-list 'load-path "/usr/share/emacs/site-lisp/chatgpt-web")
(autoload 'chatgpt-web "chatgpt-web" "Ouvrir ChatGPT Web." t)
(autoload 'chatgpt-web-new "chatgpt-web" "Nouvelle conversation ChatGPT." t)
(autoload 'chatgpt-web-open "chatgpt-web" "Reprendre une conversation ChatGPT." t)
(autoload 'chatgpt-web-conversations "chatgpt-web" "Lister les conversations ChatGPT." t)
(autoload 'chatgpt-web-login "chatgpt-web" "Connexion au site ChatGPT." t)
