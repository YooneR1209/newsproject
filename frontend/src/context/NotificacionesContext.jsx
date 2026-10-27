// Contador de noticias no leídas. Lo muestra el menú; lo actualiza Feed.jsx.
import { createContext, useContext, useState } from "react";

const NotificacionesContext = createContext({ unread: 0, setUnread: () => {} });

export function NotificacionesProvider({ children }) {
  const [unread, setUnread] = useState(0);
  return (
    <NotificacionesContext.Provider value={{ unread, setUnread }}>
      {children}
    </NotificacionesContext.Provider>
  );
}

export function useNotificaciones() {
  return useContext(NotificacionesContext);
}
