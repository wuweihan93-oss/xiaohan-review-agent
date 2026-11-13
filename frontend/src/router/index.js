import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      component: () => import('../views/StudyView.vue'),
      meta: { title: '小晗带你复习' },
    },
    {
      path: '/admin',
      component: () => import('../views/AdminView.vue'),
      meta: { title: '小晗管理后台' },
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.afterEach((to) => {
  document.title = to.meta.title || '小晗带你复习'
})

export default router
